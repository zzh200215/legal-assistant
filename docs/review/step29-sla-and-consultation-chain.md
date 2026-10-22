# 第二十九步交付说明：审核 SLA 提醒 + 咨询侧证据链

> 一句话：审核台不再只标「逾期」，还能提前提醒「24 小时内到期」并给出「剩 6 小时 / 超时 2 天」的相对读数；咨询历史多了「依据」列，把引用条数、句级溯源覆盖与忠实度一眼摊开。

- 日期：2026-10-22
- 后端：`app/services/legal/legal_workspace_service.py`（队列 SLA 字段 + `review_sla` 的 `due_soon_count` + `sla` 过滤）、`app/api/legal/legal_api.py`（`GET /review-queue?sla=overdue|due_soon`）、`app/services/legal/consultation_evidence.py`（咨询证据链摘要）
- 前端：`LegalReviewTab.vue`（计数、筛选、相对时间）、`LegalConsultationsTab.vue`（历史「依据」列 + 结果卡摘要行）
- 测试：`tests/test_legal_review_assignment.py`（SLA +2 例）、`tests/test_consultation_evidence.py`（+7 例）、`frontend/tests/e2e/u3-workflow.spec.js`（两类断言）
- 契约：openapi 快照与 `types.gen` 同步（新增 query 参数，无 breaking）

---

## 1. 审核 SLA 提醒

### 1.1 数据

队列每行新增两个读时字段（不写库）：

| 字段 | 含义 |
| --- | --- |
| `review_due_in_hours` | 距离 `review_due_at` 的小时数（负数=已超时；无截止时间=null） |
| `sla_state` | `overdue` / `due_soon`（≤24h）/ `on_time` / null |

`review_overdue` 保留原义（已超时），`review_sla` 增加 `due_soon_count`，并顺带修掉了 `overdue_rate` 重复键。

### 1.2 接口

```
GET /api/legal/review-queue?sla=due_soon   # 只看 24 小时内到期
GET /api/legal/review-queue?sla=overdue    # 等价于 overdue=true，语义更直白
```

`sla` 只接受 `overdue|due_soon`，其它值 422。

### 1.3 界面

- 页头计数：`待处理 / 逾期 / 24h内到期 / 未分配 / 平均小时`；
- 行内：逾期红标签或「24h内到期」橙标签，后面跟「超时 2 天 / 剩 6 小时」；
- 筛选下拉新增「24小时内到期」；
- 详情面板同样显示相对时间。

## 2. 咨询侧证据链

### 2.1 摘要口径

`summarize_consultation_evidence(row)` 读三处已有数据（不新增表/列）：

| 来源 | 字段 |
| --- | --- |
| `references_json` | 引用条数、`active_references`（status=active） |
| `attribution_json` | `total_sentences` / `matched_sentences` → `attributed_rate` |
| `faithfulness_json` | `available === true` 且有布尔 `faithful` 才给 `pass`/`fail`，否则 `unchecked` |

等级：无引用 → `none`；忠实度 fail → `weak`；覆盖率 ≥80% 且引用有效 → `strong`；≥50% 或只有整篇粒度 → `partial`；其余 `weak`。

### 2.2 展示

- 咨询历史表新增「依据」列：`无引用` / `1 条引用` / `1 条引用 · 3/4 句`，颜色随等级（success/warning/danger/info）；
- 结果卡「参考依据」标题下新增一行：`句级溯源 3/4 句 · 忠实度通过`（未通过时带处数，老记录显示「未生成句级溯源」）。

## 3. 验证

| 项 | 结果 |
| --- | --- |
| SLA 测试 | `?sla=due_soon` 返回 1 条且 `sla_state=due_soon`、`review_due_in_hours≈6`；`?sla=overdue` 返回空；`?sla=whatever` → 422；`review_sla.due_soon_count=1` |
| 咨询证据链测试 | 7 例：无引用 none、强（80%+有效引用）、部分（60%）、忠实度 fail 强制 weak、老记录无句级映射→partial、失效引用不能 strong、序列化挂载 summary |
| e2e（u3） | 咨询结果卡显示「句级溯源 3/4 句」、历史列表出现「依据」列与 `1 条引用 · 3/4 句`；审核队列行显示「24h内到期」「剩 6 小时」 |
| 前端 | lint 0 error、build 通过 |

## 4. 边界

1. **提醒是拉取式**：进入审核台才看到，没有站内推送/邮件；也没有「到期前 N 小时自动升级」。
2. **24 小时阈值写死在 `REVIEW_DUE_SOON_HOURS`**，未做成按组织配置。
3. **咨询证据链摘要只读、不阻断**：`none/weak` 不会挡审核通过（合同侧有门禁，咨询侧暂无）。
4. **句级覆盖率依赖生成时的词面重合**：`attribution` 是服务端算好的词典匹配结果，不是语义判定；老记录没有该字段时只能给出「整篇粒度」的 partial/weak。
5. **忠实度未校验 ≠ 不忠实**：只有判官实际跑过（`available === true`）才给 pass/fail，其余如实显示 `unchecked`。
