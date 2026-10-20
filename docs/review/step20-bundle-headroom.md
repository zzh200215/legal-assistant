# step20：包体余量治理（面板按需加载 + 预算重定）

2026-10-20 · 承接 [step10](step10-bundle-budget.md)（上一轮包体治理）

## 0 起因

总量 **1599.9 / 1600 KB**，只余 0.1 KB——任何新功能都会撞红 CI，而"下次再说"等于把红线变成随时会响的警报。这一轮把它当正事处理：**先量遍所有能做的地方，再决定改代码还是改预算**。

## 1 逐个量过的可能性

| 手段 | 实测收益 | 结论 |
| --- | --- | --- |
| CSS 合并（`build.cssCodeSplit: false`） | 总资产 **−3.2 KB** | 不做：牺牲按路由加载 CSS（首屏要拉全部样式），3 KB 不值 |
| 去掉 Sentry | **0 KB** | 生产构建 `VITE_SENTRY_DSN` 为空，SDK 已被 tree-shake（dist 里搜不到 `browserTracing`） |
| 删未用依赖 | 0 KB | 运行时依赖 6 个逐个扫描（`@sentry/vue` / `axios` / `element-plus` / `pinia` / `vue` / `vue-router`）全部有引用 |
| 图标按需 | 0 KB | `@element-plus/icons-vue` 只有 `Chat.vue` 显式用 1 枚，其余是 element-plus 组件内部在用 |
| 删死 CSS | 产物约 **−1.2 KB** | 只删了 `el-menu` / `el-statistic` 覆写（组件已零使用）；`design-tokens.css` 另有 18 个 direction B 之前的遗留类，但与在用的选择器交织在多选择器规则里，收益 ~1.4 KB、风险高于收益，留开口项 |
| **copilot 面板按需加载** | 入口 **−36.4 KB raw / −13.7 KB gzip** | 做：面板首屏不需要，且栅格列宽由 `--copilot-w` 预留，后加载不产生布局位移 |

结论：**总量没有便宜的整块可捡**（element-plus 一家占了 132 KB JS + 约 250 KB CSS），能真正改善的是入口。

## 2 改动

| 文件 | 改动 |
| --- | --- |
| `frontend/src/App.vue` | `CopilotDock` 由静态 import 改为 `defineAsyncComponent(() => import(...))`，注释写明"列宽已由 CSS 变量预留" |
| `frontend/src/styles/element-overrides.css` | 删 `el-menu*`（导航已是原生按钮）与 `el-statistic*`（统计卡已改 `.app-stat`）覆写，各留一行注释说明原因 |
| `frontend/scripts/check-bundle-budget.mjs` | 入口预算 120/42 → **100/35**（按新实测收紧）；总量 1600 → **1700**（实测 +6%）；页面 chunk 130 不动；新增 **95% 预警档**（`⚠️ 接近预算`，不判失败）并打印"已用 xx%" |

## 3 实测（`npm run build` + `npm run bundle:check`）

| 指标 | 改动前 | 改动后 | 预算 | 已用 |
| --- | --- | --- | --- | --- |
| 入口 JS (raw) | 114.8 KB | **78.4 KB** | 100 KB | 78% |
| 入口 JS (gzip) | 41.1 KB | **27.4 KB** | 35 KB | 78% |
| 最大页面 chunk | 110.9 KB（System） | 111.2 KB（System） | 130 KB | 86% |
| 总资产 (raw) | 1599.9 KB | 1605.2 KB | 1700 KB | 94% |

## 4 为什么总量反而涨了 5.3 KB

代码没删，只是**搬进了异步 chunk**：`CopilotDock` 12.2 KB + `CopilotAnswer` 10.9 KB + 共享块重新切分。分块本身还有固定开销（每个 chunk 的 import/export 表、hash 文件名），所以"入口缩小"和"总量缩小"是两件事——**这一轮换来的是首屏与入口余量，不是总资产下降**。这点必须在文档里说清，否则会让人误以为总量治理有进展。

## 5 预算政策

1. **入口收紧**：从 120/42 收到 100/35。收紧而不是放宽，是因为上一版红线（102.0）已被证明可达，而现在实测只有 78.4——不留 43 KB 的虚高余量，红线才有约束力。
2. **总量放宽到 1700**：这是唯一被放宽的一项，理由是它对应真实的功能增长（四层 copilot、WS 流式步骤、证据/溯源卡、15 个系统 tab），且已量过没有便宜的整块可捡。放宽幅度 = 实测 +6%。
3. **95% 预警档**：总量现在 94%，还没到预警线；再长 17 KB 就会出现 `⚠️ 接近预算`，在撞红之前先提醒。预警不判失败，避免"每次都红"导致大家忽略红色。

## 6 验收

- `npm run build` exit 0；`npm run bundle:check` 四项全绿（输出含"已用 %"，无 ⚠️）
- `npx eslint src tests --ext .vue,.js` → 0 error / 10 warning
- `npm run test:unit` → 84 passed
- `npx playwright test --workers=1` → **53 passed**（含 `copilot-dock` 18、`copilot-case-context` 3、`entry-convergence` 4，面板改为异步加载后契约未变）

## 7 边界与下一步

1. **总量仍是最紧的一条**（94%）。真要再降只能删功能或换重库——element-plus 是最大头（JS 132 KB + CSS 约 250 KB），全站深度使用，替换不现实。
2. **`design-tokens.css` 的 18 个遗留类**（`page-heading` / `board-card` / `table-card` / `metric-tile` / `overview-tile` / `status-chip` / `badge-chip` / `app-cite` 等）已无任何源码引用，但与在用选择器写在同一条规则里；清理需要逐条拆选择器，约 1.4 KB。留作开口项，等下次动这个文件时一起做。
3. **面板异步化的边界**：首帧右侧是空的 380px 列（列宽预留），chunk 到达后填充；离线首次访问时面板加载会失败一次（`OfflineBanner` 已在，未做面板级重试）。
