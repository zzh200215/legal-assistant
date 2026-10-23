# 第三十六步交付说明：合同台账跨案件视图

> 一句话：合同台账每一行直接显示「关联案件 / 最近审查状态 / 风险摘要（总数 · 高风险 · 无定位）」，不用点进合同或切到案件才知道。

- 日期：2026-10-23
- 后端：`app/services/legal/legal_contract_listing.py`（批量补最近审查摘要）
- 前端：`frontend/src/views/LegalContracts.vue`（台账新增三列）
- 测试：`tests/test_contract_listing_review.py`（2 例）、`frontend/tests/e2e/contracts-flow.spec.js`

---

## 1. 数据链路

台账列表 `GET /legal/orgs/{org_id}/contracts` 的每行补 `review` 字段：

- 取该合同**最新版本**里 `source_review_id` 非空的记录（按 `version_no` 倒序）；
- 用 `source_review_id` 查审查记录，读 `risks_json` 汇总；
- 批量查询（版本一次、审查一次），没有 N+1。

```json
"review": {
  "id": 71, "status": "needs_lawyer_review", "version": 2,
  "risks_total": 3, "risks_high": 1, "risks_unanchored": 2,
  "reviewed_at": null, "updated_at": "2026-10-23T06:00:00"
}
```

没有审查的合同 `review = null`。

## 2. 前端

合同台账新增三列：

| 列 | 内容 |
| --- | --- |
| 关联案件 | `#case_id`，未关联显示「未关联」 |
| 最近审查 | 审查状态标签（律师通过=绿、退回补充=红、其余=橙）；没有审查显示「未审查」 |
| 风险 | `N 条 · 高 M · 无定位 K`（高红、无定位橙）；无审查显示 `—` |

## 3. 验证

| 项 | 结果 |
| --- | --- |
| 单测 | 有审查的合同返回 review（id/status/total=2/high=1/unanchored=1）；无审查合同 review=null |
| e2e（contracts-flow） | 台账行显示「需律师审核」「3 条」「高 1」「无定位 2」；无审查行显示「未审查」；原有新建/版本/Diff/签署禁用断言不变 |
| 门禁 | 见提交说明 |

## 4. 边界

1. **只看最近一次**：历史审查不展开，点进合同页/审查页看完整记录。
2. **审查关联靠 `source_review_id`**：没有从审查生成版本的合同（纯手工录入/上传）显示「未审查」，即使审查记录标题相同。
3. **风险口径**：无定位 = `source_location.paragraph` 为空；与合同页证据链同一口径。
4. **只增列不过滤**：台账目前不支持按审查状态/风险数筛选，需要时再加 query 参数。
5. 列表仍是分页 20 条/页，摘要只覆盖当前页。
