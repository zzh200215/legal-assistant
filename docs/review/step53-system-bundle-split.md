# 第五十三步交付说明：System 页按需拆 chunk（表格 / 上传 / 图表）

> 一句话：System 页首屏只保留「健康检查 + 概览条」，其余 15 个 tab 按「运营/表格」与「观测/图表」两组异步 chunk 加载；同时把构建目标收到 ES2021，去掉旧浏览器 downlevel 辅助代码。System 路由 chunk **118.8 → 17.2 KB**，总资产 **1699.3 → 1672.7 KB**，重新留出 27.3 KB 余量。

- 日期：2026-10-28
- 前端：`views/System.vue`、新增 `components/system/SystemOpsTabs.js` / `SystemAnalyticsTabs.js`、`vite.config.js`、`scripts/check-bundle-budget.mjs`
- 测试：System e2e 5 条全过；门禁见 §5
- 无后端/数据库改动

---

## 1. 为什么现在必须拆

step52 结束时总资产 **1699.3 / 1700 KB**，只剩 0.7 KB；System 路由 chunk 自身 **118.8 KB**（页面预算 130），15 个 tab 的表格、上传、日期选择、图表代码全被打进同一个入口 chunk。下一次任何页面级改动都会直接撞总预算。

先试过「每个 tab 各自异步」，结果：

| 方案 | System chunk | 总资产 | 结论 |
| --- | --- | --- | --- |
| 不动 | 118.8 KB | 1699.3 KB | 贴线 |
| 15 个 tab 各自 chunk | 21.3 KB | 1720.3 KB | 拆小了页面，但 15 份 chunk 包装/共享模块边界把总资产顶超 |
| 两组异步 + 原构建目标 | 17.2 KB | 1701.7 KB | 仍差 1.7 KB |
| **两组异步 + ES2021** | **17.2 KB** | **1672.7 KB** | 通过，留 27.3 KB |

所以最终不是简单「多拆几个文件」，而是**分组异步 + 构建目标收敛**两件事一起做。

## 2. 分组方式

首屏静态保留：

- `SystemHealthTab`（默认 tab，首屏就要用）
- `SystemOverviewBar`（顶部指标条）

其余按语义分两组：

| chunk | 包含 tab | 主要重依赖 |
| --- | --- | --- |
| `SystemOpsTabs` | 任务中心、操作日志、异常告警、反馈闭环、工具健康、审批台、知识库、组织架构、敏感治理 | `el-table`、`el-upload`、`el-dialog`、`el-select` |
| `SystemAnalyticsTabs` | 实验观测、用户漏斗、事件漏斗、留存与北极星、Token 统计、投递看板、通知模板 | `el-table`、`el-date-picker`、`el-radio-group`、趋势/图表 |

实现：

```js
const loadOpsTabs = () => import('../components/system/SystemOpsTabs.js')
const loadAnalyticsTabs = () => import('../components/system/SystemAnalyticsTabs.js')
const asyncGroupTab = (loader, name) => defineAsyncComponent({
  loader: () => loader().then((module) => module[name]),
  delay: 0,
})
```

- 第一次点任一运营 tab → 下载 `SystemOpsTabs`；后续同组 tab 直接用缓存；
- 第一次点任一观测 tab → 下载 `SystemAnalyticsTabs`；
- 默认健康检查不会下载这两组；
- `el-tab-pane` 仍保留 `lazy`，组件只在激活时挂载，tab 自己的接口请求也仍然懒发。

## 3. 构建目标

`vite.config.js`：

```js
build: {
  target: 'es2021',
}
```

- 原默认 `modules` 会为 Safari 14 等旧浏览器生成 downlevel 辅助代码；
- ES2021 目标要求 Chrome/Edge 85+、Firefox 79+、Safari 15+；
- 这一项单独省下约 **29 KB**，是两组异步后还能回到预算内的关键；
- 这是**全站**构建目标，不是 System 页局部设置，浏览器下限变化见边界 2。

## 4. 预算脚本

`frontend/scripts/check-bundle-budget.mjs`：

- `PAGE_CHUNK_PATTERN` 增加 `SystemOpsTabs` / `SystemAnalyticsTabs`，异步分组 chunk 也按页面 chunk 预算检查，避免「改个名字绕过预算」；
- 删除未使用的 `statSync` 导入；
- 注释记录本轮实测，便于下一次改动先量产物。

## 5. 结果与验证

| 指标 | 拆前 | 拆后 | 预算 |
| --- | --- | --- | --- |
| 入口 JS raw | 81.7 KB | **80.2 KB** | 100 |
| 入口 JS gzip | 28.3 KB | **27.9 KB** | 35 |
| System 路由 chunk | 118.8 KB | **17.2 KB** | — |
| SystemOpsTabs | — | **62.4 KB** | — |
| SystemAnalyticsTabs | — | **81.8 KB** | — |
| 最大页面 chunk | 122.1 KB | **117.1 KB**（LegalWorkspace） | 130 |
| 总资产 raw | 1699.3 KB | **1672.7 KB** | 1700 |
| 总资产余量 | 0.7 KB | **27.3 KB** | — |

验证：

- `npm run build` + `node scripts/check-bundle-budget.mjs`：四项全部通过；
- System e2e 5 条全过（24.9s，mock）：
  - 投递看板（Analytics 组）
  - 实验观测/引用问题（Analytics 组）
  - 反馈处理 + 任务重试（Ops 组）
  - 通知模板（Analytics 组）
  - 组织架构（Ops 组）
- 覆盖了两组的首次加载、同组后续切换、tab 内交互与原有接口参数；
- 全量门禁见 §6。

## 6. 门禁

- `scripts/check_all.py`：**10/10，14.2 分钟，exit 0**（本树最终状态）。
- 后端全量：**2457 passed / 4 skipped**；OpenAPI **361**；前端单测 **96 passed**。
- 包体：入口 **80.2 / 100 KB**、最大页面 chunk **117.1 / 130 KB**、总资产 **1672.7 / 1700 KB**。

## 7. 边界

1. **不是每个 tab 一个 chunk**：同组第一个 tab 会下载整组（Ops 62.4 KB / Analytics 81.8 KB）；这是为了压住 chunk 包装开销与总资产，代价是首次点开比单 tab chunk 多下载同组代码。
2. **浏览器下限提高**：ES2021 → Chrome/Edge 85+、Firefox 79+、Safari 15+；Safari 14 及更旧浏览器不再保证可用。这是全站变化，不只是 System 页。
3. **首屏仍有 17.2 KB System chunk**：健康检查与概览条保持静态，避免默认 tab 额外一次网络往返；如果后续健康检查也变重，可再拆。
4. **分组是手工维护的**：新增 tab 必须显式加进某一组（或单独异步），否则会被打进 System 入口 chunk，预算脚本会拦住。
5. **组内代码不按需 tree-shake**：动态模块的命名导出会被整体打入组 chunk，未激活 tab 的组件代码也会下载，只是不挂载、不发请求。
6. **共享依赖仍由 Rollup 自动分块**：Element Plus 的 table/select/popper 等仍在共享 chunk，不随组重复；组 chunk 只含各组 tab 与专属依赖。
7. **缓存行为**：组 chunk 命中浏览器缓存后，同组切换不再请求；发版后 hash 变化会重新下载。
8. **总资产仍是 98%**：余量从 0.7 KB 回到 27.3 KB，但还没有到「可以随便加功能」的程度；下一次页面级功能仍应先量产物。
9. **无运行时行为变化**：tab 名称、路由 query、接口、权限、懒加载时机不变；e2e 覆盖两组。
10. **回滚**：删除两个分组模块、恢复 `System.vue` 静态 import、移除 `target: 'es2021'` 与预算脚本 pattern 即可回到拆前状态。

## 8. 后续建议

- 下一轮如果还要加 System 功能，优先加进现有两组之一，不要再新增第三组（每多一组约 1–2 KB 包装开销）；
- LegalWorkspace 仍是最大页面 chunk（117.1 KB），后续可单独做一轮工作台面板拆分；
- 若要支持 Safari 14，需要把 target 退回默认，总资产会回到 1701.7 KB，届时要同步删掉约 2 KB 代码或调整总预算，不能只改 target。
