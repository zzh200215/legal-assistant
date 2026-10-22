# 第二十六步交付说明：合同审查风险存储合并（清历史包袱）

> 一句话：同一批风险原来存两份——`legal_contract_reviews.risks_json`（模型输出、证据链、门禁读它）和 `contract_risk_items`（结构化实体、处置状态、claims 上游）。现在合成一份：风险与处置状态统一在 `risks_json`，风险项表删除。

- 日期：2026-10-22
- 迁移：`alembic/versions/20261022_0101_merge_contract_risk_items.py`（MySQL 已执行，head 从 `20261019_0100` → `20261022_0101`）
- 改动：`app/models/legal_domain.py`（删模型/列）、`app/services/legal/legal_domain_service.py`（派生与状态机）、`app/api/legal/legal_domain_api.py`（派生列表 + 下标动作）、`alembic/versions/20260812_0074_legal_domain_models.py`（历史迁移改自带 Core 表，不再依赖会变的 ORM）
- 测试：`tests/test_legal_domain_{model,service,api}.py`、`tests/test_migrations.py`

---

## 1. 为什么要合并

在 step25 的证据链里已经能看到症状：

- **处置写在表里**：律师「接受/缓释/忽略」一条风险，写的是 `contract_risk_items.status`；
- **缺口与门禁读 JSON**：`evidence_summary`、发布门禁、就地补定位读的是 `risks_json`。

于是同一条高风险可以「表里已忽略、JSON 里仍待审」，或者「JSON 补了定位、表里 evidence 还是旧的」。迁移前本机数据是 53 条风险 / 53 条风险项（1:1），恰好没漂移，但这是运气不是设计。

## 2. 迁移做了什么

`20261022_0101_merge_contract_risk_items`：

1. **回填**：按 `review_id` 分组、组内按 `id` 升序（= 创建时 risks 的顺序 = JSON 下标）把风险项的 `status / review_status / reviewer_id / resolved_at / resolution_note / evidence_json` 写回对应 `risks_json[i]`；只在值不同时更新，幂等。
2. **删表**：`contract_risk_items`。
3. **删列**：`legal_claims.risk_item_id`（无外键的多态列；claims 通过 `source_type + source_id` 归属审查记录）。
4. **downgrade**：恢复表结构与列，但**不恢复数据**（风险项可由 `risks_json` 重新派生）。

本机执行结果（MySQL，迁移前 head `20261019_0100`）：

| 检查 | 结果 |
| --- | --- |
| 回填后状态分布 | `high → needs_review` **24** 条；`medium/low/needs_facts → open` **29** 条（与迁移前表内状态完全一致） |
| `contract_risk_items` | 已删除（`inspect().has_table` = False） |
| `legal_claims.risk_item_id` | 已删除 |
| 53 条风险的 `status` | 53/53 有值 |

## 3. 代码怎么变

| 位置 | 变化 |
| --- | --- |
| `persist_review_artifacts` | 风险清单**只写** `risks_json`（单一存储）；高/严重风险仍派生 `risk_warning` claim + evidence（不再有 `risk_item_id`） |
| `get_risk_items` | 从 `risks_json` **派生**结构化风险项：`id = 下标 + 1`、`index`、severity/category/title/summary/evidence/recommendation/original_text_excerpt 全由风险字段映射 |
| `risk_status` | 已处置（accepted/mitigated/dismissed）以写入为准；其余按严重度派生——高风险不允许停在 `open`，旧数据里的 `open` 在这里归一为 `needs_review` |
| `update_risk_item_status` | 签名改为 `(db, user, review_id, risk_index, action, note)`，直接写回 `risks_json[risk_index]`；claim 按 `source_type + source_id + 标题/描述` 匹配同步 |
| `supersede_artifacts` | 风险清单随新版本整体替换，不再逐条标 dismissed；只把旧版本未决 claims 标为 superseded |
| `assert_publishable` | 未处理高/严重风险从 `risks_json` 统计（不再查表） |
| `get_case_domain` | 案件级 `risk_items` 由该案件下各审查的 JSON 派生 |
| `20260812_0074` | 历史迁移原本 `from app.models... import ContractRiskItem`，模型删除后会 import 失败；改为迁移自带的 Core `sa.Table`，效果（建表 + 回填）不变 |
| 前端 | 结构化风险项按 `index` 匹配选中风险（id 仍是 1 基下标），处置动作接口与载荷不变 |

## 4. 验证

| 项 | 结果 |
| --- | --- |
| 迁移执行 | `alembic upgrade head` 成功，head `20261022_0101`；迁移链单一 head（`tests/test_migrations.py` 静态校验通过） |
| 运行时抽查 | 审查 #1：`get_risk_items` 返回 3 条（id=1..3、severity/status/evidence 正确）；`serialize_workspace_row` 的 `evidence_summary` 正常；发布门禁报「未审核通过 + 1 项未处理高风险」 |
| 领域测试 | `test_legal_domain_{model,service,api}.py` + `test_migrations.py` → **53 passed** |
| 合同/工作台回归 | `test_legal_api.py` / `test_legal_workspace_module.py` / `test_contract_evidence.py` → **79 passed** |
| 前端 | 单测 **96 passed**、lint 0 error、build 通过 |
| e2e | `contract-upload`、`u3-workflow` 通过（风险项接口在用例里是 mock，不依赖表） |

## 5. 诚实边界

1. **downgrade 只恢复结构，不恢复数据**：回滚后表是空的，风险项需从 `risks_json` 重新派生（本仓库有派生函数，但迁移脚本不自动写回）。
2. **查询能力下降**：JSON 字段没法像表列那样建索引/聚合。当前所有风险查询都按单条审查（7 条记录、53 条风险）读取，够用；如果将来要「跨审查按严重度统计」，应建**派生表/物化视图**而不是回到双写。
3. **claim 关联变弱**：`legal_claims` 不再有 `risk_item_id`，与具体风险的关联靠 `source_type + source_id + 标题/描述` 匹配；本机迁移前该列为 0 行使用，风险有限。
4. **旧版本风险不单独留档**：重提后 `risks_json` 整体替换，旧风险只能从审核动作日志/版本内容回溯，没有逐条历史。
5. **迁移在真实 MySQL 上验证过，但只这一套数据**（53 条）；生产若存在 `risks_json` 与表条目数不一致的脏数据，回填只按下标对齐，多出的条目会被忽略。

## 6. 下一步

1. 若要做跨审查的风险统计，建 `contract_review_risk_view`（物化/派生），由 `risks_json` 生成，保持单一写入源。
2. 风险处置历史进 `legal_review_actions`（当前只记审核动作，不记单条风险的接受/忽略），让「谁在什么时候忽略了哪条风险」可追溯。
3. 把 `evidence_summary` 的派生函数与 `get_risk_items` 合并成同一处实现，避免两套映射再漂移。
