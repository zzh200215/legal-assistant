# 第七十一步交付说明：历史咨询加载更多（突破最近 50 条）

> 一句话：后端新增 `GET /legal/consultations/history`，按 id 倒序返回 `cursor` 之前的记录；前端历史列表在满 50 条时显示「加载更多历史咨询」，追加更早记录，并在加载后显示真实总数。

- 日期：2026-10-29
- 后端：`services/legal/legal_workspace_service.py`（复用 `search_consultations`）、`api/legal/legal_api.py`
- 前端：`api/legalWorkspace.js`、`composables/useLegalConsultations.js`、`components/legal/LegalConsultationsTab.vue`
- 测试：`tests/test_legal_workspace_case_sharing.py` +1；e2e 1 passed
- 无迁移；OpenAPI **366**（新增 1 条路径）

---

## 1. 为什么补

`list_rows` 固定 `limit(50)`：

- 历史咨询超过 50 条后，第 51 条永远看不到；
- 卡片头「共 N 条」其实只是已加载条数，容易误解；
- 搜索能搜到更早记录，但清空搜索后仍只剩最近 50 条。

这是列表能力的缺口，不是搜索问题。

## 2. 后端

```http
GET /api/legal/consultations/history?case_id=1&limit=20&cursor=123
```

- 复用 `search_consultations(q="")` 的可见性与分页逻辑：
  - 有 `case_id` 先 `verify_case_access`；
  - 否则只看本人记录；
- `cursor` 之前的记录，`id desc`；
- 返回 `{items,total,page,limit,has_more,next_cursor}`；
- 路径注册在 `/consultations/{item_id}` 之前。

## 3. 前端

- 首次仍用原列表（最近 50 条），不额外发请求；
- 只有 `consultations.length >= 50` 才认为「可能还有更早记录」，显示按钮；
- 点击「加载更多历史咨询」：
  - 用当前最后一条 id 作 cursor；
  - 追加到 `olderConsultations`，与首屏列表按 id 去重；
  - 更新 `historyTotal` 与 `historyHasMore`；
- 卡片头：
  - 搜索态仍显示「匹配 N 条」；
  - 本地态未加载更多时显示「已加载 50 条」；
  - 加载后显示「共 N 条」；
- 新提交/刷新/审核后重新加载时清空「更早记录」，避免脏数据。

## 4. 验证

后端 `test_history_endpoint_paginates`：

- 同案件 26 条，`limit=20` 第一页 20 条 + `has_more=true`；
- cursor 第二页 6 条 + `has_more=false`；
- total 正确。

e2e：

- 原历史列表（1 条）不显示「加载更多」；
- 搜索、审核记录、导出记录、声明等原断言保持。

构建：

- 入口 80.7 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB；
- 总资产 1679.6 / 1700 KB（余 20.4 KB）。

全量门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，13.4 分钟，exit 0**（本树最终状态）。
- 后端全量：**2469 passed / 4 skipped**；OpenAPI **366**；前端单测 **96 passed**。
- 包体：入口 **80.7 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1679.6 / 1700 KB**。

## 6. 边界

1. **总数初始未知**：首屏只拿到最近 50 条，未见底前显示「已加载 50 条」，点一次后才拿到 total。
2. **启发式判断**：只有满 50 条才显示按钮；刚好 50 条时会多一次空请求，之后隐藏。
3. **不自动无限滚动**：需要手动点「加载更多」，不是滚动触底加载。
4. **搜索与加载更多是两条路径**：搜索状态用服务端搜索分页；清空搜索回到本地列表 + 更早记录，两者不共享 cursor。
5. **按 id 排序**：与搜索接口一致，id 与创建时间不一致时顺序会偏。
6. **只对咨询生效**：合同审查/文书列表仍走原来的 50 条限制。
7. **证据链筛选在本地**：加载更多后仍只过滤已加载集合。
8. **无新表/迁移**：只加了一条读接口。

## 7. 后续建议

- 若要「共 N 条」首屏就准确，可再加一个 count 接口或在列表响应里带 total；
- 合同审查/文书列表有同样 50 条上限，可按本步模式复制；
- 余量 20.4 KB，下一步继续量产物。
