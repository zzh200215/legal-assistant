# 第四十四步交付说明：合同台账审查摘要物化（筛选下推数据库）

> 一句话：台账的「审查状态 / 风险」筛选不再先取 1000 条再在内存里过滤——最近一次审查的状态、风险总数、高风险、无定位计数落成一张 1:1 摘要表，列表直接 `JOIN + WHERE + COUNT + OFFSET/LIMIT`，1000 条之外也能筛出来、也能正确分页。

- 日期：2026-10-27
- 后端：`LegalContractReviewSummary` 模型 + 迁移 `20261027_0108`（建表 + 存量回填，MySQL 已执行）、`app/services/legal/contract_review_summary.py`（刷新/回填）、`legal_contract_listing.py`（查询改写）、5 处写入路径接入刷新
- 运维：`scripts/backfill_contract_summaries.py`（全量重算，支持 `--dry-run`）
- 测试：`tests/test_contract_listing_review.py`（9，含 >1000 条下推）、`tests/test_contract_summary_backfill.py`（3，直接调用迁移回填 SQL）
- 前端：无改动（响应字段不变）

---

## 1. 为什么是独立摘要表，而不是合同表加列

`legal_contracts` 带乐观锁（`__mapper_args__ = {"version_id_col": version}`）。把摘要写进合同行会顶高版本号，任何一个后台刷新都可能让正在编辑合同的人拿到 409。

所以新建 `legal_contract_review_summaries`（`contract_id` 唯一）：

| 列 | 含义 |
| --- | --- |
| `review_id` / `review_status` / `review_version` | 最近一次审查 |
| `risks_total` / `risks_high` / `risks_unanchored` | 计数口径与 step25/36 一致（high/critical；`source_location.paragraph` 为空算无定位） |
| `reviewed_at` / `review_updated_at` | 审查时间线 |
| `refreshed_at` | 摘要刷新时间（排障用） |

语义保持「版本号最大的、带 `source_review_id` 的版本所指的审查」；没有审查 → **删行**，台账显示「未审查」。

## 2. 刷新点（写入即刷新，同一事务）

| 写入路径 | 触发 |
| --- | --- |
| 创建合同版本（`source_review_id`） | `create_contract_version` |
| 从审查生成候选修改稿 | `apply-review-suggestions` |
| 审查状态流转（通过/退回/线下/归档） | `apply_review_action` |
| 风险处置（接受/缓释/忽略/撤销） | `update_risk_item_status`、`bulk_update_risk_item_status` |
| 风险就地补定位 | `POST /contract-reviews/{id}/risks/{index}/anchor` |
| 审查重跑（重提/重新生成风险） | `persist_review_artifacts` |

刷新函数只查索引（`source_review_id`、`contract_id` 唯一）并 upsert 一行，不做全表扫描。

## 3. 查询改写

```sql
SELECT contract.*, summary.*
FROM legal_contracts contract
LEFT JOIN legal_contract_review_summaries summary ON summary.contract_id = contract.id
WHERE contract.organization_id = :org
  AND (:review_status IS NULL OR summary.review_status = :review_status)
  AND (:risk = 'high'       AND summary.risks_high > 0)
   OR (:risk = 'unanchored' AND summary.risks_unanchored > 0)
   OR (:risk = 'none'       AND summary.contract_id IS NULL)
ORDER BY contract.updated_at DESC, contract.id DESC
OFFSET ... LIMIT ...
```

`total` 用同一条件 `COUNT(DISTINCT contract.id)`；不再有 `limit(1000)`，也不再有「先全量取再内存切页」。

## 4. 迁移与补数

迁移 `0108` 建表 + 三个索引，并用**纯 Core SQL** 回填（不 import ORM/服务，避免后来模型变更让老迁移崩）：按 `contract_id` 取版本号最大的带审查版本 → 读审查 `risks_json` 计数 → 批量插入。

回填函数拆成 `_backfill(bind)`，单测直接用内存库调用它验证 SQL（3 条：计数、跳过未审查、取最高版本）。

运维补数：`python -B scripts/backfill_contract_summaries.py`（`--dry-run` 只统计）。

## 5. 验证

| 项 | 结果 |
| --- | --- |
| 摘要附加 | 合同行 `review` 字段与旧实现一致（id/status/version/risks_*） |
| 筛选 | 状态筛选、`risk=high`、`risk=none` 都只返回匹配合同，`total` 正确 |
| 分页 | 筛选后 `page=2&page_size=2` 返回 1 条、`total=3` |
| **超过 1000 条** | 1001 份合同，匹配的合同是最早创建（排序最后）的那份：旧实现取前 1000 条会漏掉，新实现 `total=1` 且能返回；`risk=none` 统计到全部 1000 条未审查合同 |
| 写入路径 | 审查通过后按 `lawyer_approved` 能筛到；`persist_review_artifacts` 重写风险后计数与筛选同步 |
| 解绑 | 删除关联版本后刷新会删除摘要行，台账回到「未审查」 |
| 迁移回填 | 直接调用迁移 `_backfill`：计数正确、未审查跳过、多版本取最高版本 |
| 真实库 | 迁移执行后 `summaries=0`（本地 2 份合同都没有带审查来源的版本），与旧口径「都显示未审查」一致 |
| 回归 | 合同/审查相关 112 passed（domain service/domain api/legal api/listing/assignment） |

## 6. 边界

1. **写入点必须覆盖**：新增任何会改审查状态、风险清单或合同版本关联的写路径，都要调用刷新；漏掉会表现为台账「审查」列与详情不一致（可用补数脚本修复）。
2. **只保留最近一次审查**：历史审查不进气台账筛选；要看历史用风险处置记录/审查详情。
3. **摘要不含严重度分布**：只有 total/high/unanchored 三个计数，要按 medium/low 筛选得加列或改查审查表。
4. **没有定时对账**：不做「摘要 vs 真值」的定期校验任务，一致性依赖写路径 + 补数脚本。
5. **组织维度单列**：摘要按合同所属组织写入；合同换组织（当前没有该功能）不会自动迁移摘要。
6. **排序仍是合同更新时间**：筛选用摘要，排序不用；「按审查时间排序」需另加列与索引。
7. **旧数据只回填一次**：迁移跑完后新增的写入由代码负责；若在迁移前就有手工改库，需跑补数脚本。
8. **`source_review_id` 没有单独索引**：刷新时按它过滤版本表，当前合同版本量级小未加索引；版本数上量后应补一条索引迁移。
