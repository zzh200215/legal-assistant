# 第八十二步交付说明：案件任务视图内新建任务自动归入案件

> 一句话：任务中心带 `?case_id=N` 时，新建任务默认写入 `case_id`，创建后不会从当前筛选结果里消失；对话框顶部明示「创建后自动归入案件 #N」。

- 日期：2026-10-31
- 前端：`components/tasks/TaskCreateDialogs.vue`、`views/Tasks.vue`
- 后端：无接口变更（复用 `TaskCreate.case_id` 与 `verify_case_access`；补创建测试）
- 测试：共享/任务单测 19 passed；任务 e2e 4 passed
- 无迁移；OpenAPI **370**（路径与 schema 均不变）
- 包体：总资产 **1687.0 / 1700 KB**（余 13.0 KB）

---

## 1. 为什么补

step79 给任务加了 `case_id`，step80/81 补了显示与入口，但漏了创建链路：

- 从案件工作台「打开任务中心」进入 `?case_id=7`，点「新建任务」创建的任务没有 `case_id`；
- 创建成功后列表按 `case_id=7` 刷新，新任务立刻从当前视图消失；
- 用户会以为创建失败，或认为任务被分到了别处。

## 2. 前端

`TaskCreateDialogs.vue`：

- 新增 `caseId` prop（Number，默认 null）；
- 新建对话框顶部显示 `创建后自动归入案件 #N`（仅在有案件上下文时）；
- 提交体在有案件时附带 `case_id`，没有案件时完全不带该字段（保持原请求体不变）。

`Tasks.vue`：

```html
<TaskCreateDialogs ref="createDialogs" :loading="loading" :case-id="workflowCaseId" @refresh="handleDialogsRefresh" />
```

`workflowCaseId` 本身已从 URL 解析并做了正整数校验，非法 `case_id` 不会传到对话框。

## 3. 后端

无生产代码改动：

- `POST /tasks/` 已支持 `TaskCreate.case_id`；
- 创建前调用 `verify_case_access`，严格案件非成员返回 404 `CASE_NOT_FOUND`，不泄露案件存在性；
- `TaskOut` 返回 `case_id` 与 `case_title`。

补测试 `test_create_task_with_case_id`：

- 组织成员带 `case_id` 创建 → 200，落库 `case_id` 正确，响应带 `case_title == "共享案件"`；
- 新任务出现在 `/tasks/?case_id=` 列表里；
- 严格案件非成员创建 → 404 `CASE_NOT_FOUND`，且没有落库。

## 4. 验证

任务 e2e（案件用例）：

- 进入 `/tasks?case_id=7` → 新建任务对话框显示「创建后自动归入案件 #7」；
- 创建请求体精确等于 `{title, description, assignee, collaborators, priority, progress, case_id: 7}`；
- 创建后仍按 `case_id=7` 重新拉取列表（至少两次带案件筛选的 GET）；
- 清除案件筛选后恢复全部任务。

| 项 | 结果 |
| --- | --- |
| 共享/任务单测 | 19 passed |
| 任务 e2e | 4 passed |
| 前端单测 | 96 passed |
| lint | 0 error（12 条既有 warning） |
| 迁移 | 无（head 仍 `20261030_0114`，115 个） |
| OpenAPI | 370（无变更） |
| 包体 | 入口 81.1 / 100（gzip 28.0 / 35），最大页面 chunk 85.3 / 130，总资产 **1687.0 / 1700** |

## 5. 门禁

- `scripts/check_all.py`：**10/10，14.2 分钟，exit 0**（本树最终状态）。
- 后端全量：**2474 passed / 4 skipped**；OpenAPI **370**；前端单测 **96 passed**。
- 包体：入口 **81.1 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1687.0 / 1700 KB**。

## 6. 边界

1. **只继承当前筛选**：案件来自 URL，不在对话框里提供案件选择器；没有 `case_id` 时行为与之前完全一致。
2. **不覆盖文档/聊天提取**：「从文档整理待办」「从对话整理待办」仍按原来源建任务，不自动归入当前案件。
3. **创建后不可改案件**：`TaskUpdate` 不接受 `case_id`，建错只能删了重建（当前无删除入口）。
4. **无权案件 404**：严格案件非成员创建会收到 `CASE_NOT_FOUND`，对话框保持打开并 toast，不静默失败。
5. **任务仍按创建人归属**：`case_id` 只增加案件关联，任务可见性仍走原 owner/部门/组织/共享规则。
6. **无迁移/无接口变更**：纯前端传参 + 一条后端测试；`case_title` 仍由读时批量补。
7. **余量 13.0 KB**：总资产已用 99%，下一步加 UI 前继续先量产物。

## 7. 后续建议

- 历史任务按来源回填 `case_id`（文档 `metadata_json`、拆解子任务继承父任务）；
- 跟进任务详情补「查看来源咨询」入口（`source_type=legal_consultation` 目前只显示原始枚举值）；
- 案件任务视图标题从 `#7` 换成案件名。
