# 第六十九步交付说明：咨询搜索 cursor keyset 分页

> 一句话：搜索接口新增 `cursor`（上一页最后一条 id），按 `id desc` keyset 取下一页，返回 `next_cursor`；前端「加载更多」改用 cursor，不再依赖 offset/page。offset 模式保留兼容。

- 日期：2026-10-29
- 后端：`services/legal/legal_workspace_service.py`、`api/legal/legal_api.py`
- 前端：`components/legal/LegalConsultationsTab.vue`、`tests/e2e/workspace-prefill.spec.js`
- 测试：后端搜索测试补 cursor 断言；e2e 1 passed
- 无迁移；OpenAPI 仍 **365**（同一路径新增 query 参数）

---

## 1. 为什么补

step68 用 `page/offset`：

- 搜索期间新增/删除记录，翻页会重复或漏；
- 深分页 `OFFSET` 越来越大，数据库要跳过很多行。

历史搜索天然适合 keyset：按 id 倒序，下一页只要 `id < 上一页最后一条`。

## 2. 后端

```http
GET /api/legal/consultations/search?q=经济补偿&limit=20&cursor=301
```

- `cursor` 可选，正整数；
- 有 cursor 时：`WHERE id < cursor ORDER BY id DESC LIMIT limit+1`，多取一条判断 `has_more`；
- 返回新增 `next_cursor`：有下一页时为最后一条 id，否则 `null`；
- `total` 仍是**不含 cursor** 的总匹配数；
- 未给 cursor 时保留 `page/offset`，旧调用方不受影响；
- 排序统一改为 `id desc`（id 随创建时间递增，近似“最新在前”）。

## 3. 前端

- 首次搜索不带 cursor；
- 「加载更多搜索结果」带 `searchCursor`；
- 响应更新 `searchCursor = next_cursor`；
- 清空关键字/切换案件时重置 cursor；
- 其余防抖、搜索态、依据筛选逻辑不变。

界面与 step68 一致，不再新增截图。

## 4. 验证

后端：

- 3 条“分页”记录、`limit=2`：第一页 2 条 + `has_more=true` + `next_cursor`；
- 用 `next_cursor` 取下一页：1 条 + `has_more=false` + `next_cursor=null`；
- 原 offset 分页、空结果、越权断言保持。

e2e：

- mock 搜索响应补 `next_cursor: null`；
- 输入/清空/空结果断言保持；
- 原审核记录、导出记录、复制、状态同步、带入断言保持。

构建：

- 入口 80.7 / 100 KB；
- 最大页面 chunk 85.3 / 130 KB；
- 总资产 1689.1 / 1700 KB（余 10.9 KB）。

全量门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，15.0 分钟，exit 0**（本树最终状态）。
- 后端全量：**2468 passed / 4 skipped**；OpenAPI **365**；前端单测 **96 passed**。
- 包体：入口 **80.7 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1689.1 / 1700 KB**。

## 6. 边界

1. **按 id 不按 created_at**：极端情况下导入/回填导致 id 与创建时间不一致，顺序会偏；搜索场景可接受。
2. **cursor 是明文 id**：不泄露内容，但会暴露记录数量级；需要可改用签名游标。
3. **记录被删**：旧 cursor 仍可用，只是可能跳过已删记录；不会重复。
4. **`total` 与翻页不同步**：total 在翻页过程中可能变化，前端只把它当“匹配总数”展示。
5. **offset 仍保留**：不带 cursor 时还是 page/offset，深分页问题只在新前端路径解决。
6. **证据链筛选仍在前端**：cursor 只解决分页，不解决“本页 0 条但总数非 0”。
7. **不支持反向/跳页**：只能“加载更多”，不能跳回中间页；历史列表够用。
8. **无新路径/迁移**：OpenAPI 路径数不变。

## 7. 后续建议

- 若要做“上一页/跳页”，需要双向 cursor 或保留 offset；
- 包体余量 10.9 KB，下一步继续加前端功能前先量产物。
