# 第九十步交付说明：任务统计改为全量口径

> 一句话：任务中心的「进行中 / 已逾期 / 共享任务」不再只数当前页，而是由后端按同一组可见性过滤全量统计，随列表响应一起返回；翻页、搜索、范围/来源/案件筛选后统计仍然一致。

- 日期：2026-11-01
- 后端：`services/jobs/task_service.py`、`api/tasks/task_api.py`、`core/api_response.py`
- 前端：`views/Tasks.vue`
- 测试：任务统计单测 **1 passed**；任务 e2e **9 passed**；前端单测 **100 passed**
- 无迁移；OpenAPI 无路径/模型变化（370）；包体 **1648.9 / 1700 KB**（余 51.1 KB）

---

## 1. 为什么补

此前四张统计卡里：

- 「任务总数」用服务端 `total`，是全量；
- 「进行中 / 已逾期 / 共享任务」直接 `tasks.filter(...)`，只统计**当前页 12 条**。

结果：任务超过一页时，进行中/逾期/共享会被低估；翻页时数字还会跳变。step89 之后逾期口径已经统一，正好把统计口径一起收口。

## 2. 实现

### 后端

`paginate_visible` 抽出 `_filtered_query`（可见性 + status/source/case/keyword 过滤），列表与统计共用：

```python
rows, total, stats = paginate_visible(...)
# stats = {total, in_progress, overdue, shared}
```

- `total`：与分页 total 相同；
- `in_progress`：当前过滤下 `status=in_progress`；
- `overdue`：`due_date` 非空、`status ∈ (todo,in_progress)` 且早于「当前用户时区的现在」；
- `shared`：当前过滤下 `user_id != 当前用户`。

逾期「现在」按用户通知偏好时区（`deadline` → `all` → 默认 `Asia/Shanghai`）换算成 naive 墙上时间，与 step89 提醒口径一致。

列表响应新增可选 `stats` 字段（`paginated_payload(..., stats=...)`），其他分页接口不传，响应不变。

### 前端

- 列表请求返回后同时更新 `taskTotal` 与 `taskStats`；
- 四张卡优先用 `stats`，缺失时回退到当前页计算（兼容旧 mock/旧后端）；
- 翻页只改列表，统计不随页码变化。

## 3. 验证

后端用例：

- 当前用户 4 条 + 同组织他人 2 条，`page_size=1`：
  - `total=6`、`items=1`、`stats={total:6,in_progress:1,overdue:1,shared:2}`；
- `scope=mine`：total=4、shared=0、overdue=1；
- `q=统计命中`：total=1、stats.total=1、shared=1；
- `status=done`：stats.total=1、in_progress=0、overdue=0。

前端 e2e：

- mock 列表只回 1 条，但 `stats={total:25,in_progress:7,overdue:3,shared:5}`；
- 页面四张卡分别显示 25 / 7 / 3 / 5，列表仍只有当前页 1 条。

| 项 | 结果 |
| --- | --- |
| 任务统计单测 | 1 passed |
| 任务 e2e | 9 passed |
| 前端单测 | 100 passed |
| OpenAPI / 迁移 | 370（无变化）/ 无（head 仍 `20261030_0114`，115 个） |
| 包体 | 入口 82.9 / 100（gzip 28.8 / 35），总资产 **1648.9 / 1700** |

## 4. 门禁

- `scripts/check_all.py`：**10/10，12.2 分钟，exit 0**（本树最终状态）。
- 后端全量：**2493 passed / 4 skipped**；OpenAPI **370**；前端单测 **100 passed**。
- 包体：入口 **82.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1648.9 / 1700 KB**。

## 5. 边界

1. **统计随列表请求实时算**：每次列表多 3 个 `COUNT`，没有缓存/物化；任务量大时需要预聚合。
2. **只统计四类**：total / 进行中 / 逾期 / 共享；没有 done/cancelled 细分，也没有按优先级分布。
3. **统计跟随当前筛选**：scope、来源、案件、关键词、status 都会影响统计；切换筛选后数字会变，这是预期。
4. **共享=非本人**：当前过滤下 `user_id != 我`，不区分部门共享/组织共享；admin 视角同样按此口径。
5. **逾期按用户时区**：与 step89 一致；`due_date` 当天 23:59:59 在 23:59 前不算逾期。
6. **前端有回退**：接口没有 `stats`（旧后端/旧 mock）时退回当前页计算，不会报错，但数字仍是页内口径。
7. **无 OpenAPI 变化**：列表接口没有 response_model，新增字段不进入快照；前端按可选字段读取。
8. **无迁移**：不新增表/列。

## 6. 后续建议

- 任务量大后把统计做成按组织/用户的物化计数或缓存；
- 增加 done/cancelled/优先级分布；
- 把统计卡做成可点击筛选（点「逾期」直接筛逾期任务）。
