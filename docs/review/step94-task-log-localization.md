# 第九十四步交付说明：任务操作日志中文化

> 一句话：任务详情的「操作日志」不再显示 `created/status_changed/archived` 这类英文枚举，动作与常见详情都翻译成中文；未知动作保留原值，不吞信息。

- 日期：2026-11-01
- 前端：`utils/taskLog.js`、`components/tasks/TaskDetailDialog.vue`
- 后端：无改动（只读日志，不新增接口）
- 测试：前端单测 **105 passed**（新增 5 条）；任务 e2e **11 passed**
- 无迁移；OpenAPI 无变化（372）；包体 **1655.0 / 1700 KB**（余 45.0 KB）

---

## 1. 映射

| action | 中文 |
| --- | --- |
| `created` | 创建任务 |
| `status_changed` | 状态变更 |
| `progress_changed` | 进度更新 |
| `comment_added` | 添加评论 |
| `archived` | 归档任务 |
| `unarchived` | 恢复任务 |
| 其他 | 原值 |

详情格式化：

- 状态变更：`todo -> in_progress` → `待办 → 进行中`；`(decomposed)` → `（拆解触发）`；
- 进度变更：`20 -> 50` → `进度 20% → 50%`；
- 创建来源：`Task created (source: document)` → `来源：文档`；空/`None` → `手动创建`；
- 归档/恢复：`Task archived` → `任务已归档`；`Task unarchived` → `任务已恢复`；
- 评论：保留评论摘要原文；
- 无法识别的格式原样返回。

## 2. 实现

- 新增 `frontend/src/utils/taskLog.js`：
  - `taskLogActionLabel(action)`；
  - `taskLogDetail(action, detail)`；
  - 复用 `taskSourceLabel`，来源标签与任务卡片一致；
  - 映射表冻结，避免调用方就地修改。
- `TaskDetailDialog` 操作日志表改用它渲染动作与详情，动作列宽 110。

## 3. 验证

前端单测（新增 5 条）：

- 已知动作中文化、未知动作原值、空值空串；
- 状态变更 + 拆解标记；
- 进度百分比；
- 创建来源（document / legal_consultation / None）；
- 归档/恢复/评论回退/空详情。

任务 e2e 11 条全过，详情打开、日志表渲染未出现回归。

| 项 | 结果 |
| --- | --- |
| 前端单测 | 105 passed |
| 任务 e2e | 11 passed |
| OpenAPI / 迁移 | 372（无变化）/ 无（head 仍 `20261101_0115`，116 个） |
| 包体 | 入口 83.0 / 100（gzip 28.8 / 35），总资产 **1655.0 / 1700** |

## 4. 门禁

- `scripts/check_all.py`：**10/10，14.1 分钟，exit 0**（本树最终状态）。
- 后端全量：**2497 passed / 4 skipped**；OpenAPI **372**；前端单测 **105 passed**。
- 包体：入口 **83.0 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1655.0 / 1700 KB**。

## 5. 边界

1. **未知动作原样透传**：例如 e2e mock 的 `task.created`（带点）不会命中 `created`，仍显示原值；生产代码写的是 `created`。
2. **只覆盖当前代码产生的 6 种动作**：新增动作需同步映射表。
3. **详情不是完整审计**：状态/进度只展示变更前后值，没有操作人字段（TaskLog 没有 user_id）。
4. **评论内容原样展示**：不做敏感信息脱敏。
5. **日志无分页/筛选**：详情一次取回全部日志。
6. **无后端/契约变化**：纯前端展示层。

## 6. 后续建议

- TaskLog 增加操作人 user_id，日志展示「谁做了什么」；
- 日志分页或「查看全部」；
- 统一后端 action 枚举（避免 `task.created` 这类历史/测试值）；
- 对评论/详情做敏感信息脱敏。
