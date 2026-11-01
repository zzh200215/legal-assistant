# 第九十二步交付说明：统计卡组合筛选

> 一句话：统计卡从「互斥快捷筛选」改成「可叠加」：点「进行中」再点「已逾期」会同时下推 `status=in_progress&overdue=true`；再点一次只取消该条件，点「任务总数」清空全部。

- 日期：2026-11-01
- 前端：`views/Tasks.vue`
- 后端：无改动（step91 的 `status` + `overdue` 已可同时使用）
- 测试：任务 e2e **10 passed**；任务 API 单测覆盖组合过滤；前端单测 **100 passed**
- 无迁移；OpenAPI 无变化（370）；包体 **1652.0 / 1700 KB**（余 48.0 KB）

---

## 1. 交互变化

| 操作 | 之前 | 现在 |
| --- | --- | --- |
| 点「进行中」再点「已逾期」 | 逾期激活，进行中被清除 | 两者同时激活，URL 同时带 `status` + `overdue` |
| 再点「进行中」 | — | 只移除 `status`，逾期保留 |
| 点「共享任务」 | 清掉进行中/逾期 | 可叠加到现有条件上 |
| 点「任务总数」 | 清空 | 清空（不变） |

- 三张卡各自独立 toggle；激活态分别判断，不再互斥；
- 「任务总数」在任意筛选激活时显示「当前结果」，点击清空全部；
- 提示文案改为「统计卡可组合筛选（如「进行中 + 已逾期」）…」；
- 无结果文案按组合生成，如「当前没有进行中且逾期的任务」。

## 2. 实现

`applyStatsFilter(kind)` 不再整组重置：

```js
if (kind === 'in_progress') toggle query.status
if (kind === 'overdue')     toggle query.overdue
if (kind === 'shared')      toggle query.scope
if (kind === 'total')       delete 三者
```

- 每个分支只改自己的参数，保留另外两个；
- 改完同步 refs → `router.replace` → `fetchTasks()`；
- 路由 watcher 继续处理外部 URL 变化（后退/深链）；
- 后端 `_filtered_query` 同时应用 `status` 与 `overdue`，无需改接口。

## 3. 验证

前端 e2e：

- 点「进行中」→ `status=in_progress`；
- 再点「已逾期」→ 请求同时带 `status=in_progress&overdue=true`，两张卡都 `aria-pressed=true`；
- 再点「进行中」→ `status` 移除、`overdue` 保留；
- 点「共享」→ `scope=shared`，再点「进行中」→ 两者并存；
- 点「任务总数」→ 三者全部移除，只有总数卡激活；
- 深链 `/tasks?status=in_progress&overdue=1` → 首请求同时带两个条件，两卡激活。

后端：

- `status=in_progress&overdue=true` → 只命中「逾期进行中」1 条；
- `status=todo&overdue=true` → 0 条（证明两个条件同时生效）。

| 项 | 结果 |
| --- | --- |
| 任务 e2e | 10 passed |
| 任务 API 单测 | 组合过滤断言通过 |
| 前端单测 | 100 passed |
| OpenAPI / 迁移 | 370（无变化）/ 无（head 仍 `20261030_0114`，115 个） |
| 包体 | 入口 82.9 / 100（gzip 28.7 / 35），总资产 **1652.0 / 1700** |

## 4. 门禁

- `scripts/check_all.py`：**10/10，13.0 分钟，exit 0**（本树最终状态）。
- 后端全量：**2494 passed / 4 skipped**；OpenAPI **370**；前端单测 **100 passed**。
- 包体：入口 **82.9 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1652.0 / 1700 KB**。

## 5. 边界

1. **最多三个条件叠加**：进行中、逾期、共享；不能叠加「待办/已完成/已取消」等状态。
2. **状态仍是单选**：`status` 只有一个值；卡片只做 `in_progress`。
3. **统计随组合过滤**：总数卡显示组合后的结果数，标签为「当前结果」。
4. **共享=非本人**：`scope=shared` 语义不变。
5. **逾期按用户时区**：当天 23:59:59 之前不算逾期。
6. **URL 用 replace**：不产生历史记录。
7. **无后端/契约变化**：step91 的 `status`、`overdue` 参数已经支持组合。
8. **包体 +0.2 KB**：只改前端状态与文案。

## 6. 后续建议

- 把状态做成多选/分段控件（待办 + 进行中）；
- 增加「优先级」「来源」卡片；
- 在 URL 上支持 `status` 多值（逗号分隔）；
- 统计卡旁加「清除全部」按钮。
