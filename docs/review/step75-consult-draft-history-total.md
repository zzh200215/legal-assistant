# 第七十五步交付说明：咨询/文书标题接入各自 total

> 一句话：文书草稿标题从「已加载 N 份」改为优先用 `draftHistoryTotal`；咨询标题补上「（还有更早）」提示，并在拿到 `historyTotal` 后显示真实总数。与合同审查的 `contractHistoryTotal` 口径一致。

- 日期：2026-10-29
- 前端：`components/legal/LegalConsultationsTab.vue`、`components/legal/LegalDraftsTab.vue`
- 测试：e2e 1 passed；无后端/接口/迁移变化
- 包体：总资产 1683.2 / 1700 KB

---

## 1. 为什么补

step74 把合同审查标题改成服务端总数后，三类历史口径不一致：

- 咨询：已用 `historyTotal ?? consultations.length`，但总数未拿到时没有「还有更早」提示；
- 文书：标题一直是 `drafts.length`，即使加载更多拿到 `draftHistoryTotal` 也不用；
- 合同：已用 `contractHistoryTotal`。

用户在三类历史里看到的「N 条」含义不一致。

## 2. 改法

**文书**

```html
{{ draftHistoryTotal ?? drafts.length }} 份历史草稿
```

- `loadMoreDrafts` 返回 total 后，标题改用 total；
- 仍有更早记录时补「（还有更早）」。

**咨询**

```html
已加载 N 条（还有更早）
共 N 条（还有更早）
```

- 未拿到 `historyTotal` 时保持「已加载 N 条」，并明确还有更早；
- 加载更多拿到 total 后显示「共 N 条」；
- 搜索态仍显示「匹配 N 条」，不受影响。

## 3. 效果

- 三类历史标题都优先使用各自 `/history` 返回的 total；
- 未请求过 total 时退回已加载数，不伪造总数；
- 有更早记录时统一提示「（还有更早）」。

## 4. 验证

e2e：

- 工作台 e2e 通过（咨询历史、文书历史、合同审查面板均正常渲染）；
- 少于 50 条时不显示加载更多，标题显示已加载数。

构建：

- 入口 80.9 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB；
- 总资产 1683.2 / 1700 KB（余 16.8 KB）。

全量门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，16.8 分钟，exit 0**（本树最终状态；首次跑遇到 `test_mfa_risk_p0` 的 `test_confirm_with_wrong_code_fails` 偶发失败，单独重跑通过，随后全量复跑通过）。
- 后端全量：**2470 passed / 4 skipped**；OpenAPI **368**；前端单测 **96 passed**。
- 包体：入口 **80.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1683.2 / 1700 KB**。

## 6. 边界

1. **首屏仍无 total**：普通模式标题先显示已加载数；点加载更多后才准确。
2. **total 与列表不同步**：显示「共 N 条」时列表可能只有当前页，需要继续加载更多。
3. **咨询搜索态不显示 total**：搜索用 `searchTotal`，与历史 total 是两套口径。
4. **文书没有筛选**：total 是全部文书，不是筛选后数量。
5. **客户端依据筛选**：咨询「无引用」筛选只作用于已加载集合，total 仍是全量。
6. **无接口/迁移变化**：纯展示层。

## 7. 后续建议

- 若要首屏就准确，可加 count 接口或让列表响应带 total；
- 余量 16.8 KB，下一步继续量产物。
