# 第七十二步交付说明：合同审查 / 文书历史加载更多

> 一句话：把咨询的「50 条上限 + 加载更多」模式复制到合同审查与文书草稿：新增 `GET /legal/contract-reviews/history`、`GET /legal/drafts/history`，前端两个历史面板在满 50 条时显示「加载更多」，cursor 追加更早记录。

- 日期：2026-10-29
- 后端：`services/legal/legal_workspace_service.py`（通用 `list_history`）、`api/legal/legal_api.py`
- 前端：`api/legalWorkspace.js`、`useContractReviews.js`、`useLegalDrafts.js`、`LegalContractTab.vue`、`LegalDraftsTab.vue`
- 测试：`tests/test_legal_workspace_case_sharing.py` +1；e2e 1 passed
- 无迁移；OpenAPI **368**（新增 2 条路径）

---

## 1. 为什么补

`list_rows` 对合同审查、文书草稿同样固定 `limit(50)`：

- 第 51 条历史审查/文书永远看不到；
- 咨询已经能「加载更多」，另外两条主链路还是断的；
- 历史面板的「N 条」只是已加载条数。

## 2. 后端

通用方法：

```python
list_history(db, user, kind, *, case_id=None, limit=20, cursor=None)
```

- `kind`：`contract_review` / `draft`；
- 可见性沿用 `list_rows`：案件上下文 `verify_case_access`，否则只看本人；
- `cursor` 之前、`id desc`，多取一条判断 `has_more`；
- 返回 `{items,total,limit,has_more,next_cursor}`；
- 案件上下文附 `submitter_name`。

接口：

```http
GET /api/legal/contract-reviews/history?case_id=1&limit=20&cursor=123
GET /api/legal/drafts/history?case_id=1&limit=20&cursor=123
```

两者都注册在各自 `/{item_id}` 之前，避免 `history` 被当成 id。

## 3. 前端

**合同审查**

- `useContractReviews` 增加 `olderContractReviews`，列表按 id 去重合并；
- 首屏满 50 条才显示「加载更多历史审查」；
- 用最后一条 id 作 cursor，追加并更新 total/has_more；
- 重新加载时清空更早记录。

**文书草稿**

- `useLegalDrafts` 同样增加 `olderDrafts` 与 `loadMoreDrafts`；
- 历史面板底部显示「加载更多历史文书」；
- 标题旁提示「（还有更早）」。

**展示**

- 标题计数仍显示已加载条数，但会带「（还有更早）」；
- 不改变筛选/展开/版本加载逻辑。

## 4. 验证

后端 `test_contract_and_draft_history_paginate`：

- 各 25 条，`limit=20` 第一页 20 条 + `has_more=true`；
- cursor 第二页 5 条 + `has_more=false`；
- total 正确。

e2e：

- 原合同审查/文书列表（少于 50）不显示加载更多；
- 带入、版本、评论、审核等原断言保持。

构建：

- 入口 80.9 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB；
- 总资产 1682.0 / 1700 KB（余 18.0 KB）。

全量门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，13.5 分钟，exit 0**（本树最终状态）。
- 后端全量：**2470 passed / 4 skipped**；OpenAPI **368**；前端单测 **96 passed**。
- 包体：入口 **80.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1682.0 / 1700 KB**。

## 6. 边界

1. **总数初始未知**：首屏只拿最近 50 条，点一次加载更多后才知道 total。
2. **启发式判断**：只有满 50 条才显示按钮；刚好 50 条会多一次空请求。
3. **筛选与总数不一致**：合同审查有状态/风险筛选，标题计数是已加载集合的匹配数，不是全库匹配数。
4. **手动加载**：需要点按钮，不自动滚动加载。
5. **按 id 排序**：id 与创建时间不一致时顺序会偏。
6. **仅合同/文书/咨询**：其他列表（如任务、通知）未做。
7. **不共享 cursor**：三种历史各自维护，互不影响。
8. **无新表/迁移**：两条只读接口。

## 7. 后续建议

- 若筛选也要覆盖更早记录，需要把筛选条件下推到 `/history` 接口；
- 首屏总数可加 count 接口；
- 余量 18.0 KB，下一步继续量产物。
