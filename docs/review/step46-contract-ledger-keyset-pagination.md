# 第四十六步交付说明：台账 keyset 分页 + 排序索引（大机构化）

> 一句话：台账不再 `OFFSET` 翻页——用「排序键 + 合同 id」做游标，翻页期间新增/删除不会重复或跳条；同时给合同表与摘要表补了 5 个复合索引，`reviewed_at` 排序改为摘要表驱动，MySQL `EXPLAIN` 确认走索引范围、没有 filesort。

- 日期：2026-10-27
- 后端：`legal_contract_listing.py`（游标编解码 + 双段查询 + keyset 条件）、`legal_contract_api.py`（`cursor` 参数、400 映射）、模型复合索引、迁移 `20261027_0110`（MySQL 已执行）
- 前端：`LegalContracts.vue`（`next_cursor` 追加、加载更多、请求序号防串页）
- 测试：`tests/test_contract_listing_review.py`（16，含漂移/尾段/游标校验）、e2e `contracts-flow.spec.js`（加载更多）
- 文档：本文；step45 的截图沿用（底部新增「加载更多 · 已显示 2 / 3 份」）

---

## 1. 为什么要改：OFFSET 的两个问题

| 问题 | OFFSET 的表现 |
| --- | --- |
| 翻页漂移 | 第 1 页取完后有人新增/删除合同，第 2 页的 `OFFSET 20` 会重复或跳过记录 |
| 深翻页变慢 | 数据库要先数过前 N 条再丢弃，页数越深越慢；排序还常伴随 filesort |

keyset 分页把「第几页」换成「从哪条之后」：游标里带上一页最后一条的排序键与合同 id，下一页只查 `(排序键, id) < 游标` 的记录。位置由数据本身决定，不随前面增删而漂移。

## 2. 游标设计

游标是 base64url 编码的 JSON，对客户端不透明：

| 字段 | 含义 |
| --- | --- |
| `v` | 版本号（当前 1，升级时旧游标会被拒绝） |
| `s` / `o` | 排序字段与方向 |
| `g` | 段：`value`（排序键非空）或 `tail`（排序键为空，统一排最后） |
| `k` / `i` | 最后一条的排序键 / 合同 id |
| `n` / `t` | 已返回条数 / 第一页算出的总数 |
| `f` | 筛选指纹（org、user、case、status、q、review_status、risk 的短哈希） |

校验规则：

- 排序或方向变了 → 400「游标与当前排序不匹配」；
- 筛选变了（包括换了查询词、风险档位）→ 400「游标与当前筛选不匹配」；
- 乱码/版本不符/字段缺失 → 400「游标无效」；
- 游标不是签名凭证，伪造只能改变分页边界，**筛选与可见性条件仍然每次都重新应用**。

总数只在第一页 `COUNT` 一次，之后随游标传递，避免每翻一页都全量 count；`has_more = 已返回数 < 第一页总数`。

## 3. 双段查询：非空值一段、空值一段

排序键可能为 NULL（未审查合同的 `reviewed_at`），而 MySQL/SQLite 对 NULL 的升降序位置不一致。实现把结果拆成两段：

1. **value 段**：`排序键 IS NOT NULL`，按 `(排序键, 合同 id)` 排序，keyset 条件为
   `排序键 < 游标键 OR (排序键 = 游标键 AND 合同 id < 游标 id)`（升序反向）。
2. **tail 段**：`排序键 IS NULL`，按合同 id 排序；只有 value 段不足一页时才补 tail。

- 按审查时间排序时，value 段从摘要表出发（`FROM summaries JOIN contracts`），让摘要索引直接提供顺序；
- 按合同更新时间/创建时间排序时，从合同表出发，摘要表只用于筛选/输出；
- 所有筛选（状态、审查状态、风险、案件、可见性 EXISTS）在段查询里复用同一套 `_apply_filters`。

`updated_at` / `created_at` 理论上非空，但仍按同一套空值尾段逻辑处理，避免历史脏数据被漏掉。

## 4. 索引（迁移 0110）

| 表 | 索引 | 用途 |
| --- | --- | --- |
| `legal_contracts` | `(organization_id, updated_at, id)` | 默认排序 + 游标范围 |
| `legal_contracts` | `(organization_id, created_at, id)` | 按创建时间排序 |
| `legal_contracts` | `(organization_id, id)` | 空值尾段按 id 翻页 |
| `legal_contract_review_summaries` | `(organization_id, reviewed_at, contract_id)` | 审查时间排序（摘要表驱动） |
| `legal_contract_review_summaries` | `(organization_id, review_updated_at, contract_id)` | 审查更新时间排序 |

模型里同步声明了 `Index(...)`，SQLite 单测建表即有同样的索引名；迁移用 inspector 判断存在性，可重复执行。

## 5. SQLite 兼容

SQLite 把时间存成文本，且同一列可能混有两种格式：`CURRENT_TIMESTAMP` 写入 `2026-10-27 11:53:24`，ORM 绑定写入 `2026-10-27 11:53:24.000000`。直接字符串比较会把「相等」误判成「小于」，导致第二页重复第一页。

因此**仅 SQLite** 把排序/游标比较转成 `julianday(列)` 与 `julianday(:值)`；MySQL（生产）仍用原生 `DATETIME`，索引路径不受影响。这是开发/测试方言的兼容分支，不是生产语义。

## 6. 验证

### 6.1 单测（SQLite）

| 项 | 结果 |
| --- | --- |
| 筛选后翻页 | 3 份待审、每页 2：第一页 2 条 + `has_more`；第二页 1 条、无重复、`next_cursor=null` |
| 翻页漂移 | 5 份合同按 `updated_at` 翻页；取完第一页后把其中一条改到最前，第二页仍是原顺序的接下来两条，不重复、不跳过 |
| 审查时间双段 | 3 份已审查 + 3 份未审查、每页 4：第一页 3 已审 + 1 未审；第二页剩余 2 未审 |
| 游标校验 | 换风险筛选 → 报「筛选」；换排序 → 报「排序」；乱码 → 报「游标」；均为 `ValueError`（API 转 400） |
| 总数快照 | 第二页 `total` 沿用游标值，不再重新 count |
| 回归 | 合同/审查/回填相关 **46 passed** |

### 6.2 MySQL（临时 1200 份合同 + 1200 条摘要，验证后已清理）

| 查询 | `EXPLAIN` 结果 |
| --- | --- |
| `reviewed_at DESC` 第一页 | `summaries` 走 `ix_legal_contract_review_summaries_org_reviewed`，`type=range`、`Backward index scan`，**无 filesort** |
| `reviewed_at DESC` 第二页 | 同一索引、`type=range`、估算 251 行、`Using index condition`，**无 filesort** |
| `updated_at DESC` 第一页 | `contracts` 走 `ix_legal_contracts_org_updated_at`、`Backward index scan`，**无 filesort** |
| NULL 尾段 | 走主键倒序（`Backward index scan`）+ 摘要唯一键回查，**无 filesort** |
| 真实 keyset | 300 条数据、每页 50：第一页 50 + 第二页 50，交集 **0**，顺序衔接正确 |
| 计数 | `total=1200` 正确 |

> 说明：本地库只有 2 份合同，索引是否被优化器选中需要造数据才看得出来；上表数据是临时组织，脚本结束后已删除。

### 6.3 e2e

`contracts-flow.spec.js` 1 passed（19.7s，全 mock）：第一页返回 `next_cursor`，点击「加载更多」后带 `cursor=page-2` 请求、表格追加第三行、按钮消失；筛选变化后回到第一页。

### 6.4 门禁

见 §8（本树跑完回填）。

## 7. 边界

1. **只能向后翻**：没有「上一页/跳页」；回看要重新加载第一页（筛选变化也会重置）。
2. **游标绑定筛选与用户**：换筛选、换排序、换账号都会 400；前端应丢弃旧游标重新加载。
3. **总数是快照**：`total` 来自第一页；翻页期间新增/删除不会实时更新 `total` 与 `has_more`，新插入且排在游标之前的记录本轮看不到（符合 keyset 语义，不属于丢数据）。
4. **索引只保排序，不保过滤**：`q` 的 `LIKE '%…%'`、风险/审查状态过滤仍要逐行判断，选择性差时扫描量仍大；`COUNT` 也仍是全量条件计数，超大组织可考虑 `with_total=false`。
5. **同键靠 id 兜底**：同一时间戳的记录按 id 同方向排序；翻页期间新插入同时间记录可能落在游标之后，下一轮才出现。
6. **NULL 段按 id 排**：`reviewed_at` 为空的合同在尾段里不按审查时间（本来也没有），只按 id。
7. **SQLite 走 `julianday`**：函数比较用不上索引，SQLite 仅开发/测试；生产 MySQL 不受影响。
8. **API 破坏性变更**：`page` 参数已移除，响应不再有 `page`；FastAPI 会忽略未知的 `page`，旧客户端若仍传 `page=2` 会静默拿到第一页——接入方需改用 `next_cursor`。
9. **游标未签名**：可被伪造，但只影响分页边界，筛选与可见性在每次查询重新应用；不构成越权。
10. **每页条数可变**：中途改 `page_size` 游标仍可用，但 `seen/total` 的「已显示」进度按新页大小继续累加。

## 8. 门禁

- `scripts/check_all.py`：**10/10，13.3 分钟，exit 0**（本树最终状态；后端 2445 passed / 4 skipped）。
- 包体：总资产 **1687.3 / 1700 KB（99%）**，`LegalContracts` chunk 15.72 KB raw（5.31 KB gzip）；OpenAPI 路径 **356**；前端单测 **96 passed**。

## 9. 回滚

- 代码：`cursor` 缺失即第一页；回滚到 step45 的 offset 实现需要同时恢复 `page` 参数与前端分页逻辑。
- 数据库：`alembic downgrade 20261027_0109` 删除 5 个索引（不删列、不动数据）。
