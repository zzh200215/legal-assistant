# 第七十七步交付说明：历史计数缓存 + 覆盖索引核验

> 一句话：给 `/legal/history-counts` 加 5 秒进程内 TTL 缓存（可按环境变量调整/关闭）；同时用 `EXPLAIN` 确认三类 COUNT 已经在走 `Using index`，现有 `user_id` / `case_id` 索引就是覆盖索引，不再新增重复索引。

- 日期：2026-10-30
- 后端：`services/legal/history_counts_cache.py`、`services/legal/legal_workspace_service.py`、`core/config/reliability.py`
- 测试：`tests/test_legal_workspace_case_sharing.py` +1（缓存命中/清空）
- 无迁移；OpenAPI 仍 **369**
- 前端无变化

---

## 1. 为什么补

`/history-counts` 每次工作台进入都会打三个 `COUNT`：

- 数字很小，但工作台刷新、切案件、切 tab 会重复请求；
- 上一轮只做了接口，没有缓存；
- 索引方面需要确认：三类表已有 `user_id` / `case_id` 单列索引，是否还需要额外覆盖索引。

## 2. 缓存

新增 `HistoryCountsCache`：

- 进程内 `OrderedDict` + 互斥锁；
- TTL 默认 **5 秒**，容量 2048；
- key：`user:{user_id}:case:{case_id|none}`；
- 只缓存计数，不缓存正文/记录；
- 配置：

```python
LEGAL_HISTORY_COUNTS_CACHE_TTL_SECONDS: int = Field(default=5, ge=0, le=300)
```

- `0` 关闭缓存；
- 服务层先查缓存，未命中再执行三个 `COUNT` 并回填。

## 3. 覆盖索引核验

在 MySQL 开发库对 6 条 COUNT 做 `EXPLAIN`：

```text
legal_consultations       user_id  ref  ix_legal_consultations_user_id      Using index
legal_contract_reviews    user_id  ref  ix_legal_contract_reviews_user_id   Using index
legal_drafts              user_id  ref  ix_legal_drafts_user_id             Using index
legal_consultations       case_id  ref  ix_legal_consultations_case_id      Using index
legal_contract_reviews    case_id  ref  ix_legal_contract_reviews_case_id   Using index
legal_drafts              case_id  ref  ix_legal_drafts_case_id             Using index
```

- InnoDB 二级索引天然包含主键，`COUNT(*)` 只读索引即可；
- 因此**不再新增** `(user_id, id)` / `(case_id, id)` 这类重复索引，避免增加写入成本；
- 若未来查询条件变成多列组合（如按状态+案件计数），再评估复合索引。

## 4. 验证

后端：

- 首次 `GET /history-counts?case_id=...` → 咨询 1；
- 直接插入一条咨询后再次请求 → 仍返回 1（命中缓存）；
- `history_counts_cache.clear()` 后请求 → 返回 2；
- 原 count 可见性/越权测试保持。

测试隔离：每个用例 `setUp/tearDown` 清空缓存，避免跨用例污染。

构建/门禁见 §5。

## 5. 门禁

- `scripts/check_all.py`：**10/10，18.3 分钟，exit 0**（本树最终状态）。
- 后端全量：**2472 passed / 4 skipped**；OpenAPI **369**；前端单测 **96 passed**。
- 包体不变：1684.3 / 1700 KB。

## 6. 边界

1. **进程内缓存**：多 worker 各自一份，TTL 内不同 worker 可能返回不同计数。
2. **最多 5 秒陈旧**：新建记录后服务端缓存不会主动失效；前端 invalidation 可能仍命中旧缓存。
3. **缓存 key 含用户与案件**：不会跨用户/跨案件串数据；案件权限在计算前校验。
4. **容量上限 2048**：超出按 LRU 淘汰；只是计数，内存占用很小。
5. **覆盖索引是现有索引**：没有新增迁移；如果以后查询条件变化，需要重新 EXPLAIN。
6. **无 Redis**：不做跨实例共享；Redis 不可用/多实例场景下 TTL 语义仍成立。
7. **无接口/前端变化**：纯后端优化。

## 7. 后续建议

- 若需要跨 worker 一致，可把计数缓存放进 Redis（TTL 5s）；
- 若 count 变成复杂筛选，再按实际 WHERE 建复合索引；
- 余量 15.7 KB，下一步继续量产物。
