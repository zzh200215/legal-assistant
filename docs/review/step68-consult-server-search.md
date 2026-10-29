# 第六十八步交付说明：咨询历史服务端搜索与分页

> 一句话：新增 `GET /legal/consultations/search`，在服务端按问题/答复/法源搜索并做 offset 分页（`total/has_more`）；前端搜索改为 300ms 防抖调接口，结果可「加载更多」，不再只过滤客户端已加载的 50 条。

- 日期：2026-10-29
- 后端：`services/legal/legal_workspace_service.py`、`api/legal/legal_api.py`
- 前端：`api/legalWorkspace.js`、`components/legal/LegalConsultationsTab.vue`
- 测试：`tests/test_legal_workspace_case_sharing.py` +1；e2e 1 passed
- 无迁移；OpenAPI **365**（新增 1 条路径）

---

## 1. 为什么补

step67 的搜索是纯前端过滤，有两个硬边界：

- `list_rows` 默认只取最近 **50** 条，搜索范围也就只有这 50 条；
- 历史咨询增长后，前端全量过滤既搜不全，也拖慢首屏。

这一轮把搜索下沉到数据库，并补分页。

## 2. 后端

```http
GET /api/legal/consultations/search?q=经济补偿&case_id=1&page=1&limit=20
```

- `q`：1–100 字符，匹配 `question / advice / references_json`；
- `case_id`：可选；有案件上下文时先 `verify_case_access`，否则只看本人记录（与 `list_rows` 一致）；
- `page` 1–1000，`limit` 1–50，默认 20；
- 返回：

```json
{
  "items": [],
  "total": 42,
  "page": 1,
  "limit": 20,
  "has_more": true
}
```

- 排序 `created_at desc, id desc`；
- 用 `icontains(..., autoescape=True)`，用户输入的 `%/_` 不会被当通配符；
- 案件上下文下附 `submitter_name`；
- 路径注册在 `/consultations/{item_id}` 之前，避免 `search` 被当成 id。

## 3. 前端

- 搜索输入仍是一个原生 `<input>`；
- 输入后 **300ms 防抖** 调 `searchLegalConsultations`；
- 第一页覆盖，后续「加载更多搜索结果」追加；
- 搜索中显示「正在搜索…」，失败显示后端 detail；
- 清空关键字立即回到本地最近列表（不发请求）；
- 依据筛选继续在搜索结果上做前端 AND；
- 卡片头搜索态显示「匹配 N 条」，本地态显示「共 N 条」；
- 请求返回时校验关键字未变，避免旧响应覆盖新输入；
- 切换案件时清空搜索结果并按需重搜。

![历史咨询：服务端搜索（匹配 1 条）](step68-consult-server-search.png)

## 4. 验证

后端 `test_search_consultations_paginates_and_scopes_by_case`：

- 同案件 3 条“分页”记录，`limit=2` 第一页 2 条 + `has_more=true`；
- 第二页 1 条 + `has_more=false`；
- 搜不到时 `total=0`；
- 外部组织成员访问该案件 → 403/404。

e2e：

- mock `/legal/consultations/search`：`经济补偿` 返回 1 条，其他返回 0；
- 输入后显示 1 行；
- 输入不存在关键字 → 空状态；
- 清空 → 回到本地 1 行；
- 原审核记录/导出记录/复制/状态同步/带入断言保持。

| 项 | 结果 |
| --- | --- |
| 后端测试 | 13 passed（含搜索分页） |
| e2e | 1 passed / 13.6s |
| 构建 | 入口 80.7 / 100；最大页面 chunk 85.3 / 130；总资产 **1689.1 / 1700**（余 10.9 KB） |
| 门禁 | 见 §5 |

## 5. 门禁

- `scripts/check_all.py`：**10/10，15.5 分钟，exit 0**（本树最终状态）。
- 后端全量：**2468 passed / 4 skipped**；OpenAPI **365**；前端单测 **96 passed**。
- 包体：入口 **80.7 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1689.1 / 1700 KB**。

## 6. 边界

1. **offset 分页会漂移（step68 当时）**：搜索期间新增/删除记录，翻页可能重复或漏；step69 已加 cursor keyset，offset 仅保留兼容。
2. **只搜三个字段**：问题、答复、`references_json`；不搜分类、状态、审核备注、提交人。
3. **LIKE 不是全文检索**：`%keyword%` 无法用普通索引，数据量很大时仍会慢；需要全文索引/搜索引擎再升级。
4. **大小写与分词**：`icontains` 只做大小写折叠，不做中文分词/纠错/同义词。
5. **`q` 必填且最多 100 字符**：空关键字不会走服务端搜索，仍用本地最近 50 条。
6. **`page` 上限 1000**：深分页没有限制总数，但超过 1000 页会 422；如需更深应改 cursor。
7. **依据筛选仍在内存**：服务端结果先取一页，再按证据链过滤，可能出现“本页 0 条但总数不为 0”；要严格需把 evidence_level 也下推。
8. **无高亮/相关度排序**：仍按时间倒序，不按命中相关度。
9. **权限沿用 list_rows**：案件上下文校验案件访问权，个人上下文只看本人；外部组织 403/404。

## 7. 后续建议

- 数据量继续增长时把 `references_json` 的 LIKE 换成全文索引，或把 `evidence_level` 也下推到搜索；
- 深分页改 keyset：step69 已完成 cursor/next_cursor；
- 包体余量 10.9 KB，下一步继续加前端功能前先量产物。
