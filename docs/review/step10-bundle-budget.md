# Step 10：包体预算处置（方向 D）

> 背景：`docs/review/2026-10-project-diagnosis.md` 的 30 天清单里，视觉项虽然达成，但 `npm run bundle:check`
> 一直是红的（CI 里 `.github/workflows/ci.yml` 会跑它）。2026-10-18 用户选择「D-1 降体积 + D-2 重标定」两者，
> 实际执行顺序是**先降体积**：降完四项全部达标，于是 D-2 不再需要放宽红线，只留下书面记录。

## 0. 做什么 / 不做什么

**做**：定位入口与总资产的真实构成 → 消除把整库拖进产物的写法 → 用原生 CSS 替掉最后两处 element-plus 栅格 →
复测四项预算并留证据。

**不做**：不动预算红线（不放宽、不下调）；不引入新的构建插件或压缩手段（brotli/外部 CDN 等）；
不改任何页面行为与文案；不为「好看的数字」删功能。

## 1. 起点与终点（`node scripts/check-bundle-budget.mjs --report`）

| 指标 | 治理前 | 治理后 | 预算 | 结果 |
| --- | --- | --- | --- | --- |
| 入口 JS (raw) | 145.6 KB ❌ | **102.0 KB** | 120 KB | ✅ |
| 入口 JS (gzip) | 50.0 KB ❌ | **35.8 KB** | 42 KB | ✅ |
| 最大页面 chunk | System 121.9 KB ✅ | System **121.8 KB** | 130 KB | ✅ |
| 总资产 (raw) | 1642.8 KB ❌ | **1579.0 KB** | 1600 KB | ✅ |

`npm run bundle:check` → exit 0（`[bundle] 全部预算通过`）。入口 raw 减少 **43.6 KB（−30%）**，总资产减少 **63.8 KB**。

## 2. 三个根因与手法

### 2.1 桶导入把整库拖进入口（主因）

**32 个文件**写着 `import { ElMessage } from 'element-plus'`（还有 `LegalReviewTab.vue` 里一处模板未使用的
死导入 `import { ElButton, ElInput } from 'element-plus'`）。element-plus 的包根导出会引入整库模块图
（含各组件的 CSS 副作用），因此一个 `ElMessage` 就把大量用不到的组件与其样式拉进产物。

修法：全部改为**按组件深导入 + 显式样式**（仓库既有约定，例如 `LegalSourcesTab.vue` 早已这么写）：

```js
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/message/style/css'
```

`message` / `message-box` 两类共 32 处由一次性脚本改写，改完 `frontend/src` 里 `from 'element-plus'` 根导入**归零**。

### 2.2 main.js 里的零使用全局注册

`frontend/src/main.js` 全局注册了 `ElAside/ElContainer/ElHeader/ElMain/ElMenu/ElMenuItem`，全仓模板**一处都没用**。
删掉它们的 import、`*/style/css` 与注册项（保留 `ElButton/ElIcon`：12 个文件用 `<el-button>`、4 个用 `<el-icon>`
且未本地导入，依赖全局注册）。

### 2.3 最后两处 el-row/el-col → 原生 CSS 栅格

`el-col.css` 是 761 条 `.el-col-1..24` 规则、约 **35.6 KB**，而全仓只剩两处使用者：

- `frontend/src/components/legal/LegalSourcesTab.vue`（新建/编辑资料对话框表单）：4 行 → `.source-form-row`
  （`repeat(2, minmax(0,1fr))`）/ `.source-form-row.is-three`（3 列）/ `.source-form-cell`，≤720px 单列。
- `frontend/src/components/system/SystemOrgTab.vue`（组织/部门/用户归属）：3 行 → `.system-block-row`
  / `.system-block-cell` / `.is-split`（5:7，对应原 `:span="10/14"`），≤900px 单列。

两处都删掉了 `ElRow/ElCol` 的 import 与 `row|col/style/css`，`el-col.css` 随之从产物中消失。

## 3. 验收证据

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 预算门禁 | `npm run bundle:check` | exit 0，四项全绿（见 §1 表） |
| Lint | `npx eslint src --ext .vue,.js` | **0 error / 10 warning**（治理前 13 warning） |
| 单测 | `npm run test:unit` | **71 passed / 0 failed** |
| 构建 | `npm run build` | exit 0（17s 级） |
| 端到端 | `E2E_RUN_INTEGRATION=true npx playwright test --workers=1` | **50 passed / 0 failed**（4.2m，含 5 条真后端集成用例） |
| 栅格视觉 | 真后端实拍（法务/管理员账号） | `step10-grid-orgs.png`（3 行栅格：2 列 → 5:7 → 5:7）<br>`step10-grid-source-dialog.png`（对话框：2 列 ×3 行 + 3 列 ×1 行） |

DOM 断言（临时实拍脚本，跑完即删）：`ORG_ROWS 3 / ORG_CELLS 6`、`DIALOG_ROWS 4 / DIALOG_CELLS 9`，与替换前的
栅格列数一一对应。

**一次 flake 记录**：首次全量 e2e 是在 `eslint` + `node --test` + `bundle:check` 三个重命令**同时跑**的情况下启动的，
跑到后半程 dev server 掉线（8 条用例 `connect ECONNREFUSED ::1:5173`，`tasks-flow` / `u3-workflow` / 4 条集成用例失败）；
单独重跑（不再并发重命令）即 **50 passed / 0 failed**。结论：与包体改动无关，属既有的「复用 dev server 被挤掉」类 flake。

## 4. 决策记录

1. **先降体积，再决定是否重标定**：D-1 达标后 D-2 的「放宽红线」失去理由；把本次实测写进
   `frontend/scripts/check-bundle-budget.mjs` 的预算注释（保留 120/42/130/1600 不变），使后来者知道这组数字**被证明可达**。
2. **不用「预算 override」蒙混**：脚本支持 `ENTRY_JS_RAW_KB` 等环境变量覆盖，本次一次都没用。
3. **不为达标砍功能**：被删的只有零使用的注册与桶导入写法；`ElButton/ElIcon` 的全局注册保留。
4. **两处栅格用原生 CSS 而非「把 el-row 藏进异步 chunk」**：后者只是把 35.6 KB 挪个位置，仍会随页面加载。

## 5. 开口项

1. `el-table` 相关 CSS 仍是总资产里的大头（多张数据表页面），若后续还要压总资产，应从表格样式按需化入手。
2. `vendor-sentry-*.js` 在未配 `VITE_SENTRY_DSN` 时是 0 B 的空壳 chunk（已被摇树），但文件仍会生成——无害，未处理。
3. 页面 chunk 最大值（System 121.8 KB）离 130 KB 只剩 8 KB 余量，继续往系统页加图表/大表时要留意。
4. **教训（与包体无关但同轮发生）**：绝不用 PowerShell `Get-Content`/`Set-Content` 往返改仓库文本文件——
   控制台码页会把中文写成乱码（本轮 `SystemOrgTab.vue` 中招，靠 GBK 反向解码 + 按行号重写 11 行才救回）。
   改文件一律用编辑工具或 Node 脚本。
