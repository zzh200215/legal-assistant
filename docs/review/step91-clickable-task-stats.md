# 第九十一步交付说明：统计卡点击筛选

> 一句话：四张任务统计卡改成按钮，点「进行中 / 已逾期 / 共享任务」直接把筛选条件下推服务端并写回 URL，再点一次取消；「任务总数」在筛选时显示为「当前结果」，点它清空全部统计筛选。

- 日期：2026-11-01
- 后端：`services/jobs/task_service.py`、`api/tasks/task_api.py`
- 前端：`views/Tasks.vue`
- 契约：OpenAPI 重新导出（`GET /tasks/` 新增 `overdue` 查询参数，路径仍 370）
- 测试：任务 API 单测 **24 passed**；任务 e2e **10 passed**；前端单测 **100 passed**
- 无迁移；包体 **1651.8 / 1700 KB**（余 48.2 KB）

---

## 1. 交互

| 卡片 | 点击后 | 再次点击 |
| --- | --- | --- |
| 任务总数 / 当前结果 | 清空 status/overdue/scope，显示全部 | 无变化 |
| 进行中 | `status=in_progress` | 取消 |
| 已逾期 | `overdue=1`（清掉 status/scope） | 取消 |
| 共享任务 | `scope=shared`（清掉 status/overdue） | 取消 |

- 四张卡是互斥快捷筛选：激活一张会清掉另外两张的条件；
- 激活的卡片有高亮边框 + `aria-pressed=true`；
- 卡片下方有一行「点击统计卡筛选…再次点击取消」提示；
- 筛选写入 URL（`status` / `overdue` / `scope`），刷新、分享、浏览器前进后退都能还原；
- 无结果文案分别提示「当前没有逾期任务」「暂无进行中的任务」「暂无共享任务」。

## 2. 后端

`GET /tasks/` 新增：

| 参数 | 说明 |
| --- | --- |
| `overdue` | 布尔；只看 `due_date` 非空、`status ∈ (todo,in_progress)` 且早于「当前用户时区的现在」 |

- 逾期口径与 step89 提醒、step90 统计一致（用户通知偏好时区，默认 `Asia/Shanghai`）；
- 过滤在 `_filtered_query` 内完成，列表、分页 total、stats 共用；
- `overdue` 非法值由 FastAPI 返回 422；
- 统计仍按当前过滤计算：点「已逾期」后 `stats.total = stats.overdue = 3`，所以总数卡标签切换为「当前结果」，避免把 3 误读成全部任务数。

## 3. 前端

- 统计卡从 `div` 改为 `<button type="button">`，保留 `.app-stat` 版式，增加 hover/active/focus-visible 样式；
- 新增 `statusFilter` / `overdueFilter` 状态，列表请求带 `status`、`overdue`；
- 点击卡片时同步更新 refs、URL 并请求；`router.replace` 不新增历史记录；
- `scopeOptions` 补上「共享任务」（此前后端支持 `scope=shared`，但下拉没有入口）；
- 移除 `watch(scopeFilter)` 的隐式 URL 写入，改由下拉/卡片显式写 URL，避免与卡片筛选互相覆盖；
- 路由 watcher 处理 `status`/`overdue`/`scope` 的外部变化（后退、深链）。

## 4. 验证

后端：

- `overdue=true` 只返回 1 条逾期进行中；逾期已完成/已取消/未来/无日期都不返回；
- `overdue=false`（默认）返回全部 5 条；
- `overdue=maybe` → 422；
- stats 与列表 total 一致（1/1）。

前端 e2e：

- 点「进行中」→ 请求带 `status=in_progress`，URL 带 `status`，卡 `aria-pressed=true`；
- 点「已逾期」→ 请求带 `overdue=true`，`status` 从 URL/请求移除；
- 点「共享任务」→ 请求带 `scope=shared`，`overdue` 移除；
- 再点「共享任务」→ 取消，URL 无 `scope`；
- 深链 `/tasks?overdue=1` → 首次请求带 `overdue=true`，逾期卡激活。

| 项 | 结果 |
| --- | --- |
| 任务 API 单测 | 24 passed |
| 任务 e2e | 10 passed |
| 前端单测 | 100 passed |
| OpenAPI / 迁移 | 370（新增 `overdue` 参数，快照已重导）/ 无（head 仍 `20261030_0114`，115 个） |
| 包体 | 入口 82.9 / 100（gzip 28.8 / 35），总资产 **1651.8 / 1700** |

## 5. 门禁

- `scripts/check_all.py`：**10/10，12.9 分钟，exit 0**（本树最终状态）。
- 后端全量：**2494 passed / 4 skipped**；OpenAPI **370**；前端单测 **100 passed**。
- 包体：入口 **82.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1651.8 / 1700 KB**。

## 6. 边界

1. **四张卡是互斥快捷筛选**：不能同时选「进行中 + 逾期」；需要组合筛选时用范围下拉/URL 组合。
2. **只暴露进行中**：`status` 支持 todo/done/cancelled，但卡片只做「进行中」；其他状态可手工拼 URL。
3. **统计随卡片过滤**：点逾期后总数卡显示 3 而非全部 25，所以标签改成「当前结果」；这是刻意口径。
4. **共享=非本人**：`scope=shared` 与后端 `_visible_filter` 一致，不区分部门/组织共享。
5. **逾期按用户时区**：当天 23:59:59 之前不算逾期。
6. **URL 用 replace**：不产生历史记录；后退回到上一次实际筛选。
7. **无迁移/无新表**：只加查询参数与前端状态。
8. **包体 +2.9 KB**：按钮语义、激活态样式、提示行与「共享任务」选项。

## 7. 后续建议

- 支持「进行中 + 逾期」组合筛选；
- 统计卡点第二次时保留高亮但显示清除按钮；
- 把 `status` 全枚举做成下拉/分段控件；
- 统计改为全量聚合接口（当前仍是随列表实时 count）。
