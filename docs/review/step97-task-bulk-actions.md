# 第九十七步交付说明：任务批量完成 / 归档 / 改优先级

> 一句话：表格多选后可批量「标记完成、归档、修改优先级」；完成允许 owner 和负责人，归档/优先级仅 owner；无权限或已归档任务计入 skipped，一次最多 100 条。

- 日期：2026-11-02
- 后端：`api/tasks/task_api.py`、`services/jobs/task_service.py`、`schemas/task.py`
- 前端：`views/Tasks.vue`、`utils/taskLog.js`
- 契约：OpenAPI 重新导出（**374 paths**，新增 `POST /tasks/bulk-action`）
- 测试：批量操作单测 **4 passed**；任务 e2e **15 passed**；前端单测 **106 passed**
- 无迁移（仍 117 个，head `20261102_0116`）；包体 **1660.9 / 1700 KB**（余 39.1 KB）

---

## 1. 接口

`POST /tasks/bulk-action`

```json
{ "task_ids": [1, 2], "action": "complete" }
{ "task_ids": [1, 2], "action": "archive" }
{ "task_ids": [1, 2], "action": "priority", "priority": "high" }
```

| action | 权限 | 行为 |
| --- | --- | --- |
| `complete` | owner 或负责人 | `status=done`、`progress=100`，写状态/进度日志 |
| `archive` | 仅 owner | `archived_at=now`，写归档日志；已归档跳过 |
| `priority` | 仅 owner | 改 `priority`，写 `priority_changed` 日志；已归档跳过 |

- `task_ids` 1–100 条，自动去重；
- 非 owner / 负责人无权 / 不存在 / 已归档 → `skipped`；
- `action=priority` 缺 `priority` → 400；非法值 → 422；
- 返回 `{updated, skipped, action}`。

## 2. 前端

- 批量条新增「批量操作」下拉：标记完成 / 归档 / 修改优先级；
- 选「修改优先级」时出现高/中/低下拉；
- 点「执行」提交并刷新；成功提示「已完成/已归档/已修改优先级 X 项，跳过 Y 项」；
- 完成与批量改派共用同一条操作区，互不干扰；
- 操作日志新增「优先级变更」，详情 `high -> low` 显示为「高 → 低」。

## 3. 验证

后端：

- owner 完成自己的 2 条 + 他人 1 条 → updated=2、skipped=1；他人任务不变；
- 负责人完成指派给自己的任务 → updated=1；
- 共享成员归档 → updated=0、skipped=1，任务未归档；
- owner 归档 → `archived_at` 非空 + 归档日志；
- 优先级 medium→high + 日志；已归档任务完成 → skipped；
- 缺 priority 400、非法 priority 422、空 task_ids 422。

前端 e2e：

- 表格选 2 行 → 「批量操作」选「标记完成」→ 执行 → POST `action=complete`，提示「已完成 2 项」；
- 再次选中 → 选「修改优先级」→ 高 → 执行 → POST `action=priority, priority=high`，提示「已修改优先级 2 项」。

| 项 | 结果 |
| --- | --- |
| 批量操作单测 | 4 passed |
| 任务 e2e | 15 passed |
| 前端单测 | 106 passed |
| OpenAPI / 迁移 | **374** / 117（无新迁移） |
| 包体 | 入口 83.1 / 100（gzip 28.8 / 35），总资产 **1660.9 / 1700** |

## 4. 门禁

- `scripts/check_all.py`：**10/10，15.4 分钟，exit 0**（本树最终状态）。
- 后端全量：**2512 passed / 4 skipped**；OpenAPI **374**；前端单测 **106 passed**。
- 包体：入口 **83.1 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1660.9 / 1700 KB**。

## 5. 边界

1. **仅表格当前页**：没有跨页全选，看板不支持批量操作。
2. **完成允许负责人**：负责人可批量完成指派给自己的任务；归档/优先级仍 owner-only。
3. **已归档任务跳过**：完成/优先级不处理归档任务；归档已归档任务也 skipped。
4. **无二次确认**：点「执行」立即生效，归档不可撤销（可去归档视图恢复）。
5. **无逐条失败原因**：只返回 updated/skipped 数量。
6. **完成固定进度 100**：不会保留原进度；状态日志/进度日志各写一条（有变化时）。
7. **无批量取消完成/恢复归档**：只做完成、归档、改优先级三个动作。
8. **无异步任务**：100 条以内同步执行。
9. **包体 +1.6 KB**：批量操作下拉、优先级下拉与日志映射。

## 6. 后续建议

- 批量取消完成/恢复归档/改截止；
- 跨页全选与按筛选全选；
- 二次确认与逐条失败原因；
- 批量操作进后台任务 + 进度。
