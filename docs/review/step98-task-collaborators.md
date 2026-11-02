# 第九十八步交付说明：任务协作者真实用户化

> 一句话：协作者从自由文本升级为真实用户关联（`task_collaborators`）；owner 可多选同组织成员，协作者可推进状态/进度和评论，但不能改标题/截止/负责人/归档；旧文本协作者继续可读。

- 日期：2026-11-02
- 后端：`models/task.py`、`schemas/task.py`、`services/jobs/task_service.py`、`api/tasks/task_api.py`
- 前端：`components/tasks/TaskCreateDialogs.vue`、`components/tasks/TaskDetailDialog.vue`、`views/Tasks.vue`
- 迁移：`20261102_0117_task_collaborators.py`（第 **118** 个，head `20261102_0117`）
- 契约：OpenAPI 重新导出（374 paths，`collaborator_user_ids` 字段）
- 测试：协作者单测 **7 passed**；任务 e2e **16 passed**；前端单测 **106 passed**
- 包体 **1662.6 / 1700 KB**（余 37.4 KB）

---

## 1. 数据模型

新增 `task_collaborators`：

| 列 | 说明 |
| --- | --- |
| `task_id` | FK tasks，ondelete CASCADE |
| `user_id` | FK users |
| `created_at` | 加入时间 |

- `UNIQUE(task_id, user_id)` 防重复；
- `Task.collaborators` 旧文本字段保留为**显示名快照**，新数据写真实用户后同步写名字；
- 旧任务只有文本协作者时，`collaborator_user_ids=[]`，仍正常展示/导出；
- 迁移可逆，SQLite 不建 FK（与现有迁移风格一致）。

## 2. 指派规则

| 操作 | 规则 |
| --- | --- |
| 创建时多选协作者 | 必须是同组织用户；跨组织 400 `TASK_ASSIGNEE_INVALID` |
| owner 替换协作者 | 传 `collaborator_user_ids` 全量替换；文本快照同步 |
| owner 清空 | 传 `[]`；关联删除、文本置空 |
| 旧文本协作者 | 继续可读；owner 重新选择后替换为真实用户 |
| 非 owner 改协作者 | 403 `TASK_EDIT_FORBIDDEN` |

## 3. 权限

| 角色 | 状态/进度 | 评论 | 标题/截止/协作者/负责人/归档 |
| --- | --- | --- | --- |
| owner | ✅ | ✅ | ✅ |
| 负责人 | ✅ | ✅ | ❌ |
| 协作者 | ✅ | ✅ | ❌ |
| 同组织共享成员 | ❌ 403 | ❌ 403 | ❌ |

- 协作者与负责人共用「执行权」；管理权仍归 owner；
- 详情提示按角色显示「你是任务负责人 / 协作者」；
- 协作者不能归档/改优先级（批量操作也按 owner-only 处理）。

## 4. 前端

- 新建任务：协作者改为多选组织成员（`multiple` + `collapse-tags`）；成员为空时回退文本输入；
- 详情：owner 看到协作者多选，负责人/协作者只读；旧文本协作者显示「历史协作者：张三、李四（重新选择后替换为真实用户）」；
- 任务卡/列表继续显示 `collaborators` 名称快照；
- 保存时只在选择变化时提交 `collaborator_user_ids`，避免旧文本任务被误清空。

## 5. 验证

后端：

- 创建带真实协作者 → `collaborator_user_ids` 与显示名正确；
- 跨组织协作者 400；
- 协作者可改状态/进度、可评论；
- 协作者改标题/改派 403；
- 无关成员更新 403；
- owner 替换/清空协作者，关联与文本同步；
- 旧文本协作者创建后 `collaborator_user_ids=[]`、文本保留。

前端 e2e：

- 新建任务多选「协办律师 / 实习律师」→ POST `collaborator_user_ids=[16,17]`，不带 `collaborators` 文本；
- 既有详情协作更新用例保持通过（旧文本路径）。

| 项 | 结果 |
| --- | --- |
| 协作者单测 | 7 passed |
| 任务 e2e | 16 passed |
| 前端单测 | 106 passed |
| OpenAPI / 迁移 | **374** / **118**（head `20261102_0117`） |
| 包体 | 入口 83.1 / 100（gzip 28.8 / 35），总资产 **1662.6 / 1700** |

## 6. 门禁

- `scripts/check_all.py`：**10/10，13.3 分钟，exit 0**（本树最终状态）。
- 后端全量：**2519 passed / 4 skipped**；OpenAPI **374**；前端单测 **106 passed**。
- 包体：入口 **83.1 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1662.6 / 1700 KB**。

## 7. 边界

1. **协作者不收到通知**：到期/逾期提醒仍只发负责人（无负责人回退 owner）；协作者没有站内/邮件通知。
2. **协作者不进入「我的」**：`scope=mine` 仍只包含我创建的 + 指派给我的，不含我作为协作者的任务。
3. **单层协作者**：没有协作者组/权限级别；所有协作者权限相同。
4. **旧文本不可反查用户**：历史姓名无法自动映射为 user_id，需 owner 重新选择。
5. **成员接口最多 100 人**：超大组织多选可能不全。
6. **评论/日志仍显示 user_id**：没有显示协作者姓名（TaskLog 无 user_id，评论只有 user_id）。
7. **无批量加协作者**：一次一个任务；批量操作未覆盖协作者。
8. **删除用户/离职**：关联仍指向原 user_id，不自动清理或转交。
9. **包体 +1.7 KB**：多选组件、历史提示与权限文案。

## 8. 后续建议

- 协作者站内/邮件通知（被加入任务时）；
- 「我参与的」筛选与统计；
- 评论/日志显示操作人姓名；
- 批量加/移除协作者；
- 协作者权限分级（只读/可评论/可推进）。
