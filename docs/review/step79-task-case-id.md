# 第七十九步交付说明：Task 补 case_id，案件任务视图

> 一句话：`tasks` 表新增 `case_id`（迁移 0114），TaskCreate/TaskOut/序列化/列表筛选全部接通；咨询生成的跟进任务自动带上案件；任务中心从案件工作台进入时按 `case_id` 过滤，并显示「仅看案件 #N」可清除。

- 日期：2026-10-30
- 后端：`models/task.py`、迁移 `20261030_0114`、`schemas/task.py`、`services/jobs/task_service.py`、`api/tasks/task_api.py`、`api/legal/legal_api.py`
- 前端：`views/Tasks.vue`、`components/legal/LegalConsultationsTab.vue`（无新组件）
- 测试：后端共享/任务测试 + 任务 e2e 4 passed；工作台 e2e 1 passed
- OpenAPI **370**（schema 变更，路径数不变）

---

## 1. 为什么补

step78 的跟进任务只能靠 `description` / `source_type` 关联咨询：

- Task 没有 `case_id`，案件工作台「任务中心」入口虽然带了 `case_id`，任务列表并不按案件过滤；
- 案件维度无法统计/筛选任务；
- 从咨询生成的任务也无法进入案件任务视图。

## 2. 后端

**迁移 `20261030_0114`**

- `tasks.case_id` 可空、索引、MySQL 外键 → `legal_cases.id`；
- SQLite 只加列+索引（方言不支持 ALTER 约束）；
- 幂等：已有列/无表时跳过。

**模型/ schema**

- `Task.case_id`；
- `TaskBase.case_id`（TaskCreate/TaskOut 继承）。

**TaskService**

- `create(..., case_id=None)` 写入；
- `serialize_task` 输出 `case_id`；
- `paginate_visible(..., case_id=None)` 下推 `Task.case_id == case_id`；
- `GET /tasks/?case_id=...` 支持案件过滤。

**权限**

- `POST /tasks/` 带 `case_id` 时先 `verify_case_access`，不能把任务挂到无权案件；
- 咨询跟进任务写入 `row.case_id`，与咨询同案件。

## 3. 前端

- `Tasks.vue` 读取 `route.query.case_id`，列表请求带 `case_id`；
- 页头显示「仅看案件 #N ×」，点击清除 query 并恢复全部任务；
- `case_id` 变化时重置分页并重新拉取；
- 空状态显示「案件 #N 暂无任务」；
- 案件工作台原有「打开任务中心」链接（带 `case_id`）现在真正生效。

## 4. 验证

后端：

- 咨询跟进任务 `Task.case_id == 咨询.case_id`；
- `GET /api/tasks/?case_id=当前案件` 能查到该任务；
- 另一个案件过滤不到；
- 外部组织创建带 `case_id` 的任务 → 404（verify_case_access）。

任务 e2e：

- `/tasks?case_id=7` 只显示案件任务；
- 显示「仅看案件 #7 ×」；
- 点击后恢复全部任务，chip 消失。

| 项 | 结果 |
| --- | --- |
| 后端单测 | 18 passed（共享/导出/任务） |
| 任务 e2e | 4 passed |
| 工作台 e2e | 1 passed |
| 迁移 | 115 个，head `20261030_0114` |
| OpenAPI | 370（schema 变更） |
| 包体 | 入口 81.1 / 100，最大页面 chunk 85.3 / 130，总资产 **1685.5 / 1700**（余 14.5 KB） |

## 5. 门禁

- `scripts/check_all.py`：**10/10，15.8 分钟，exit 0**（本树最终状态）。
- 后端全量：**2473 passed / 4 skipped**；OpenAPI **370**；前端单测 **96 passed**。
- 包体：入口 **81.1 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1685.5 / 1700 KB**。

## 6. 边界

1. **case_id 可空**：历史任务/文档/聊天生成任务没有案件，不会出现在案件视图。
2. **只按案件过滤，不做案件权限继承**：任务可见性仍走原 `paginate_visible`（本人/组织/部门/共享），案件过滤只是附加条件。
3. **不能通过任务列表反查案件详情**：只显示案件 id，不额外拉案件标题。
4. **创建时校验，更新时不能改 case_id**：TaskUpdate 未暴露该字段，避免迁移中的任务被误改。
5. **无回填**：已有任务不会自动补 case_id；只有新生成/新建任务带。
6. **SQLite 无 FK**：迁移在 SQLite 仅列+索引，外键只在 MySQL 建。
7. **任务来源标签未扩展**：`legal_consultation` 仍显示原始字符串，前端映射表没加中文。
8. **无新接口路径**：复用 `/tasks/`。

## 7. 后续建议

- 任务列表展示案件标题（需批量查 case 或用 denormalized title）；
- 给历史任务做一次性回填（按 source_type/source_id 推导）；
- 余量 14.5 KB，下一步继续量产物。
