# 第一百零五步交付说明：任务优先级筛选

> 一句话：任务列表新增「优先级」筛选（高/中/低），与状态、逾期、范围、搜索、排序可组合；筛选下推 SQL 并写回 URL `priority`。

- 日期：2026-11-02
- 后端：`services/jobs/task_service.py`、`api/tasks/task_api.py`
- 前端：`views/Tasks.vue`
- 契约：OpenAPI 重新导出（375 paths，`GET /tasks/` 新增 `priority` 参数）
- 测试：优先级单测 **3 passed**；任务 e2e **19 passed**；前端单测 **106 passed**
- 无迁移（仍 119 个，head `20261102_0118`）；包体 **1668.7 / 1700 KB**（余 31.3 KB）

---

## 1. 接口

`GET /tasks/?priority=high`

| 值 | 含义 |
| --- | --- |
| `high` | 高 |
| `medium` | 中 |
| `low` | 低 |
| 不传 | 全部 |

- 参数用 `Literal` 校验，非法值 422；
- 与 `status`、`scope`、`overdue`、`archived`、`q`、`sort` 组合；
- 统计随筛选后的 `q` 计算（优先级也影响统计）。

## 2. 前端

- 工具栏排序旁新增优先级下拉（高/中/低，可清空）；
- 选择后写 URL `priority`，刷新列表，返回/分享可恢复；
- 空状态文案包含「优先级高/中/低」；
- 点任务总数卡会同时清除 status/overdue/scope/priority；
- 统计卡激活态与「当前结果」标签把 priority 视为筛选条件。

## 3. 验证

后端：

- `priority=high` 只返回高优先级任务；
- `priority=medium&status=in_progress` 组合只返回命中任务；
- `priority=urgent` → 422。

前端 e2e：

- 选择「高」→ 请求带 `priority=high`，URL `priority=high`，列表保留高优先级任务；
- 全量任务 e2e 19 passed。

| 项 | 结果 |
| --- | --- |
| 优先级单测 | 3 passed |
| 任务 e2e | 19 passed |
| 前端单测 | 106 passed |
| OpenAPI / 迁移 | **375** / 119（无新迁移） |
| 包体 | 入口 83.3 / 100（gzip 28.8 / 35），总资产 **1668.7 / 1700** |

## 4. 门禁

- `scripts/check_all.py`：**10/10，14.1 分钟，exit 0**（本树最终状态）。
- 后端全量：**2534 passed / 4 skipped**；OpenAPI **375**；前端单测 **106 passed**。
- 包体：入口 **83.3 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1668.7 / 1700 KB**。

## 5. 边界

1. **只有优先级单值**：不能多选「高+中」。
2. **无优先级统计卡**：统计仍只有总数/进行中/逾期/共享/参与。
3. **不影响看板列**：看板仍按状态分列，优先级只过滤数据。
4. **URL 只存单值**：非法值回退为全部。
5. **与统计卡叠加**：点进行中/逾期不会清优先级，点总数才清空。
6. **包体 +1.0 KB**：下拉与 URL/空状态逻辑。

## 6. 后续建议

- 优先级多选；
- 按截止日期范围筛选；
- 按负责人/协作者筛选；
- 保存筛选视图。
