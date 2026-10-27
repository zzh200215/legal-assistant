# 第四十七步交付说明：台账搜索索引化（MySQL ngram 全文索引）

> 一句话：台账的 `q` 不再只对 `title` 做 `LIKE '%关键词%'` 全表扫描——MySQL 8 上给 `title / counterparty / contract_no` 建 ngram 全文索引，查询改成布尔模式短语匹配；SQLite/单字查询回退到三列 LIKE。前端补了搜索框，300ms 防抖，搜索词参与游标指纹。

- 日期：2026-10-27
- 后端：`legal_contract_listing.py`（搜索条件分支）、`legal_contract_api.py`（`q` 长度限制与描述）、迁移 `20261027_0111`（FULLTEXT + ngram，MySQL 已执行）
- 前端：`LegalContracts.vue`（搜索框、防抖、q 随筛选/游标传递）
- 测试：`tests/test_contract_listing_review.py`（18，含标题/相对方/编号/单字/通配符/搜索翻页）、e2e `contracts-flow.spec.js`（搜索请求与结果）

---

## 1. 原来的问题

```sql
WHERE legal_contracts.title LIKE '%关键词%'
```

- `%…%` 用不上 B-Tree 索引，组织合同一多就是全表/全组织扫描；
- 只搜标题，用户按相对方或合同编号找不到；
- 前端甚至没有搜索框，`q` 只有 API 能传。

## 2. MySQL：ngram 全文索引 + 布尔短语

迁移 `0111` 在 MySQL 上执行：

```sql
ALTER TABLE legal_contracts
  ADD FULLTEXT INDEX ft_legal_contracts_search
  (title, counterparty, contract_no) WITH PARSER ngram;
```

查询侧：

```sql
MATCH (legal_contracts.title, legal_contracts.counterparty, legal_contracts.contract_no)
AGAINST (:contract_search IN BOOLEAN MODE)
```

- 用户输入先归一空白，再用双引号包成**短语**：`"劳动合同"`；
- `"` 与 `\` 会被转义，`+ - * ( ) ~ @` 等布尔操作符在短语里按字面量处理——避免用户输入变成查询语法；
- `ngram_token_size` 默认 2，所以「劳动合同」被切成 `劳动 / 动合 / 合同`，短语匹配要求这些 gram 连续出现；
- 单字（如「劳」）或非字母数字输入无法命中 ngram，走 LIKE 回退；
- 三列任一命中即可，搜索范围明确为「名称 / 相对方 / 编号」。

### 为什么不用前缀索引

前缀索引只能加速 `q%`，而台账搜索的实际输入多是名称中间的词（「劳动」「科技」「2026」），`LIKE '%…%'` 仍然用不上；ngram 全文索引才是子串检索的正解。

## 3. 非 MySQL / 短查询回退

```python
LegalContract.title.contains(keyword, autoescape=True)
or LegalContract.counterparty.contains(keyword, autoescape=True)
or LegalContract.contract_no.contains(keyword, autoescape=True)
```

- SQLite（开发/测试）没有 ngram 全文索引，直接用三列 LIKE；
- `autoescape=True` 把 `%`、`_` 当字面量，避免用户输入 `%` 匹配全部；
- MySQL 上仅单字/纯符号查询走这条回退，量少时成本可接受。

## 4. 前端

- 筛选条新增「搜索名称/相对方/编号」输入框，`type=search` 自带清空；
- 输入 300ms 防抖后重载第一页（`q` 变化会重置游标）；
- `q` 进入 `_filters_fingerprint`：带着旧游标换搜索词会 400，前端因重置也不会复用；
- 请求序号继续防止搜索/翻页回包串页。

![合同台账：搜索框 + 严重度分布 + 加载更多（e2e mock 数据）](step45-contract-ledger-severity.png)

## 5. 验证

### 5.1 单测（SQLite）

| 项 | 结果 |
| --- | --- |
| 标题搜索 | `q=劳动` / `q=" 劳动 "` 命中「劳动合同模板」 |
| 相对方 | `q=科技` 命中相对方为「某科技公司」的合同 |
| 合同编号 | `q=HT-2026-002` 命中对应合同 |
| 单字 | `q=劳` 走 LIKE 回退，仍命中 |
| 通配符 | `q=%` 不再匹配全部，按字面量处理 |
| 无结果 | `q=不存在的关键词` 返回空 |
| 搜索翻页 | 3 条「劳动事务」、每页 2：第二页 1 条、无重复；换成 `q=采购` 复用旧游标报「筛选不匹配」 |
| 回归 | 台账 18 passed |

### 5.2 MySQL（临时 300 份合同，验证后已清理）

| 查询 | 结果 |
| --- | --- |
| `q=劳动合同` | 100 条（标题命中） |
| `q=科技` | 100 条（相对方命中） |
| `q=HT-PUR-` | 100 条（编号前缀命中） |
| `q=HT-PUR-0001` | 1 条（编号精确命中） |
| `q='"'` | 0 条，无 SQL 报错（引号已转义） |
| `q=+劳动` | 100 条，`+` 被当作短语字面量，不产生布尔语法 |
| `q=不存在的词` | 0 条 |
| 搜索 + keyset | `q=劳动` 每页 20：两页各 20、交集 0、`total=100` |
| `EXPLAIN` 全文查询 | `type=fulltext`、`key=ft_legal_contracts_search`，`Extra` 含 `Using filesort`（只对命中的百来行排序） |
| `EXPLAIN` 单字回退 | `type=range` 走组织索引 + `LIKE` 过滤（预期扫描，量小） |

> 全文查询的 filesort 是对「已经由全文索引筛出的少量命中行」按台账排序键排序，不是对全表排序；如果以后要按相关度排，可另加 `ORDER BY MATCH(...) DESC`。

### 5.3 e2e

`contracts-flow.spec.js` 1 passed（14.5s，全 mock）：输入「采购」后请求带 `q=采购`、表格只剩采购协议；清空后回到第一页 2 行。

### 5.4 门禁

见 §8（本树跑完回填）。

## 6. 边界

1. **依赖 MySQL 8 + ngram 解析器**：非 MySQL 方言走 LIKE；MySQL 5.7 或未装 ngram 的实例迁移会失败，部署前要确认。
2. **ngram 粒度 2**：单字查询回退 LIKE；两字以上才走索引。修改 `ngram_token_size` 会改变索引与查询行为，需要重建索引。
3. **短语匹配，不是模糊搜索**：不支持同义词、错别字、分词外的模糊匹配；输入「劳动 合同」按包含空格的短语处理。
4. **不做相关度排序**：仍按用户选的合同时间/审查时间排序；全文索引只负责筛，不负责 rank。
5. **结果范围扩大到三列**：标题、相对方、合同编号任一命中即返回，和旧版「只搜标题」不同。
6. **布尔语法被禁用**：`+`、`-`、`*` 等按字面量处理，高级检索语法不开放；引号/反斜杠会转义。
7. **索引是 DDL 变更**：大表加 FULLTEXT 索引可能耗时/占空间，需要在维护窗口执行；本地 300 条无法代表生产耗时。
8. **迁移与代码的部署顺序**：先跑 `0111` 建索引，再上使用 `MATCH` 的代码；只回滚迁移而不回滚代码会让搜索报「找不到 FULLTEXT 索引」。
9. **大小写/停用词跟随库配置**：本地排序规则是 `utf8mb4_0900_as_cs`，英文编号区分大小写；ngram 的停用词表也可能影响个别词。
10. **搜索翻页仍受 keyset 约束**：q 变了必须从第一页重来；搜索结果的总数也沿用第一页快照。

## 7. 回滚

- 代码：删除 `_search_filter` 的 MySQL 分支即可回到三列 LIKE（无需改表）。
- 数据库：`alembic downgrade 20261027_0110` 删除全文索引；**必须先回滚代码**，否则 `MATCH` 查询会因缺少 FULLTEXT 索引报错。
- 数据：只加索引，不改数据，无回填。

## 8. 门禁

- `scripts/check_all.py`：**10/10，17.3 分钟，exit 0**（本树最终状态；后端 2447 passed / 4 skipped）。
- 包体：总资产 **1687.8 / 1700 KB（99%）**，`LegalContracts` chunk 16.11 KB raw（5.44 KB gzip）；OpenAPI 路径 **356**；前端单测 **96 passed**。
