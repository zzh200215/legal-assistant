# 第一百零三步交付说明：任务评论编辑 / 删除

> 一句话：评论作者可编辑/删除自己的评论，任务 owner 可删除任意评论；编辑/删除都写任务日志；详情页支持行内编辑与二次确认删除。

- 日期：2026-11-02
- 后端：`services/jobs/task_service.py`、`api/tasks/task_api.py`、`schemas/task.py`
- 前端：`components/tasks/TaskDetailDialog.vue`、`api/task.js`、`utils/taskLog.js`
- 契约：OpenAPI 重新导出（**375 paths**，新增 `PATCH/DELETE /tasks/{id}/comments/{comment_id}`）
- 测试：评论单测 **5 passed**；任务 e2e **17 passed**；前端单测 **106 passed**
- 无迁移（仍 119 个，head `20261102_0118`）；包体 **1666.1 / 1700 KB**（余 33.9 KB）

---

## 1. 接口

`PATCH /tasks/{task_id}/comments/{comment_id}`

```json
{ "content": "更新后的评论" }
```

`DELETE /tasks/{task_id}/comments/{comment_id}` → 204

| 操作 | 权限 |
| --- | --- |
| 编辑 | 仅评论作者 |
| 删除 | 评论作者，或任务 owner |
| 其他可见成员 | 403 `TASK_COMMENT_FORBIDDEN` |

- 空内容 → 400 `TASK_COMMENT_INVALID`；
- 评论不存在/不属于该任务 → 404 `TASK_COMMENT_NOT_FOUND`；
- 编辑写 `comment_updated` 日志；删除写 `comment_deleted` 日志（不保存被删正文）。

## 2. 前端

- 评论右上角：作者/owner 看到「编辑 / 删除」；
- 编辑：行内 textarea + 保存/取消，保存后刷新评论与日志；
- 删除：`ElMessageBox` 二次确认，确认后刷新；
- 日志动作新增「编辑评论 / 删除评论」中文化；
- 共享只读成员看不到操作按钮。

## 3. 验证

后端：

- 作者编辑自己的评论成功，内容更新；
- 其他成员编辑/删除 → 403；
- owner 删除任意评论 → 204，列表变空；
- 作者删除自己的评论 → 204；
- 空内容 400、不存在的评论 404。

前端 e2e：

- 打开详情 → 评论「旧评论」；
- 点编辑 → 填写 → 保存 → PATCH body `{content:'更新后的评论'}`，列表更新；
- 点删除 → 确认 → DELETE，评论消失；
- 全量任务 e2e 17 passed。

| 项 | 结果 |
| --- | --- |
| 评论单测 | 5 passed |
| 任务 e2e | 17 passed |
| 前端单测 | 106 passed |
| OpenAPI / 迁移 | **375** / 119（无新迁移） |
| 包体 | 入口 83.3 / 100（gzip 28.8 / 35），总资产 **1666.1 / 1700** |

## 4. 门禁

- `scripts/check_all.py`：**10/10，13.4 分钟，exit 0**（本树最终状态）。
- 后端全量：**2528 passed / 4 skipped**；OpenAPI **375**；前端单测 **106 passed**。
- 包体：入口 **83.3 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1666.1 / 1700 KB**。

## 5. 边界

1. **owner 只能删不能改**：编辑权限仅作者，owner 也不能改他人正文。
2. **无编辑历史**：日志只记「编辑评论 + 新内容前 120 字」，不保留旧版本。
3. **删除不保留正文**：日志只记 `comment #id`，不可恢复。
4. **无 @ 提醒 / 回复**：评论仍是平铺列表。
5. **无分页**：评论全量返回，大量评论时需分页。
6. **作者被移出协作者后仍可编辑自己的旧评论**（只要还能看到任务）。
7. **包体 +2.3 KB**：编辑态、确认框与日志映射。

## 6. 后续建议

- 评论 @ 提醒；
- 评论分页/按人筛选；
- 编辑历史版本；
- 评论附件。
