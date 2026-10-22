# 第二十七步交付说明：跨审查风险统计（派生视图）

> 一句话：不新建表、不加写入，把多条合同审查的 `risks_json` 在读取时汇总成一份统计——风险总数、等级分布、状态分布、高发条款、定位率、高风险待处理——前端在合同页展开「风险统计」即可看。

- 日期：2026-10-22
- 后端：`app/services/legal/contract_risk_stats.py`（聚合函数）、`app/api/legal/legal_api.py`（`GET /api/legal/contract-reviews/risk-stats`）
- 前端：`LegalContractTab.vue` 新增「风险统计」折叠区；`useContractReviews` 增加按需加载；`legalWorkspace.getContractRiskStats`
- 测试：`tests/test_contract_risk_stats.py`（7 例）、`frontend/tests/e2e/contract-upload.spec.js`（统计面板断言）
- 契约：`docs/openapi-snapshot.json` 重基线（新增 7 条路由，全部 additive）

---

## 1. 接口

```
GET /api/legal/contract-reviews/risk-stats?case_id=<可选>
```

| 参数 | 作用域 |
| --- | --- |
| 不传 `case_id` | 本人工作台记录（`list_rows` 默认按提交人隔离） |
| 传 `case_id` | 该案件下可见记录（案件成员可见，`verify_case_access` 由 `list_rows` 内部校验） |

返回（`{success, data}` 包装，`data` 示例）：

```json
{
  "reviews": 2, "risks": 3,
  "by_severity": {"critical": 0, "high": 2, "medium": 1, "low": 0},
  "by_status": {"needs_review": 2, "open": 1, "accepted": 0, "mitigated": 0, "dismissed": 0},
  "by_clause_type": [{"clause_type": "breach", "count": 2}, {"clause_type": "payment", "count": 1}],
  "anchored": 2, "unanchored": 1, "located_rate": 0.6667,
  "high_total": 2, "pending_high": 2, "reviews_with_gaps": 1,
  "by_evidence_level": {"strong": 1, "partial": 1, "weak": 0, "none": 0},
  "scope": "case", "case_id": 3, "generated_at": "2026-10-22T03:00:00+00:00"
}
```

## 2. 口径（与证据链、门禁一致）

- **等级**：`risk_level` 不在 `low/medium/high/critical` 里的一律按 `medium` 计。
- **状态**：走 `risk_status()`（step26 的归一）——已处置（accepted/mitigated/dismissed）以写入为准，其余按严重度派生，高风险不会算成 `open`。
- **定位**：取风险上服务端下发的 `evidence.anchored`（不是前端重算），`located_rate = anchored / risks`。
- **高风险待处理**：等级 high/critical 且状态 open/needs_review。
- **有缺口的审查**：`evidence_summary.unanchored > 0` 的记录数。
- **证据链等级分布**：按每条审查的 `evidence_summary.level` 计数（strong/partial/weak/none）。
- **高发条款**：`clause_type` 计数取前 5。

## 3. 为什么是「读取时聚合」而不是 SQL 视图

- 数据量：本机 7 条审查 / 53 条风险，Python 一遍扫描毫秒级；
- 可移植：测试用 SQLite、生产用 MySQL，JSON 函数两边差异大；一个纯函数两边都能用；
- 单一实现：聚合口径只有一份（`summarize_contract_risk_stats`），前端不重算。

如果将来跨审查数据上万条，再把它换成**物化表/定时快照**——写入源仍是 `risks_json`，不回到双写。

## 4. 前端

合同页历史审查下方的「风险统计」折叠区（`<details>`）：

- 默认收起，**展开时才请求**（`@toggle` → `loadRiskStats`），切案件清空重取；
- 五个数字块：风险总数 / 高＋严重 / 高风险待处理（>0 标橙）/ 无条款定位（>0 标橙）/ 定位率；
- 四条等级进度条（critical 红、high 橙、medium 主色、low 灰），宽度按占比；
- 「高发条款」标签行（前 5）；
- 空态：「暂无风险数据，完成一次合同审查后这里会汇总各条风险。」

## 5. 验证

| 项 | 结果 |
| --- | --- |
| 聚合单测 | 7 例：空输入、等级/状态归一（high+open → needs_review）、dismissed 保留、定位率、高风险待处理、条款 Top5、未知等级回落 medium |
| 接口测试 | 本人记录统计正确；他人看不到（空统计）；无权限案件 403/404 |
| e2e | `contract-upload`：展开统计 → 断言风险总数 3、`2 条审查 · 当前案件`、high 计数 2、请求携带 `case_id=3` |
| 契约 | `openapi-snapshot.json` 重基线（7 条新增路由全部 additive，无 breaking）；`types:check` 一致 |
| 门禁 | 见本次提交说明（`scripts/check_all.py`） |

## 6. 边界

1. **只统计前 50 条审查**：复用 `list_rows` 的 `limit(50)`，不是全库统计。
2. **按读取时扫描**：没有缓存/物化，每条审查的 `risks_json` 都要反序列化；数据量大时需要物化视图。
3. **没有时间维度**：不按「近 30 天 / 本周新增」切片，也不做趋势；需要时加 `created_from/created_to` 过滤。
4. **只看得到自己/案件范围**：统计的可见性完全继承 `list_rows`，不会跨租户聚合。
5. **未覆盖咨询/文书**：这是合同审查专用视图，咨询与起草的风险提示不在其中。
