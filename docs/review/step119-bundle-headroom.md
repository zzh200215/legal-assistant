# 第一百一十九步交付说明：包体治理（腾出 38 KB 余量）

> 一句话：把三处只当「文件选择器」用的 el-upload 换成原生 `<input type=file>`，生产构建改用 terser 压缩，总资产 **1689.1 → 1651.3 KB**（−37.8 KB），余量从 10.9 KB 回到 **48.7 KB**。

- 日期：2026-11-14
- 前端：`views/Documents.vue`、`components/legal/LegalContractTab.vue`、`components/legal/LegalSourcesTab.vue`、`composables/useDocumentList.js`、`vite.config.js`、`playwright.config.js`、`package.json`
- 后端：无改动
- 契约：OpenAPI **376 paths / 178 schemas / 28 error codes**（无接口变化）
- 迁移：**无新增**（仍 123 个，head `20261112_0122`）
- 测试：前端单测 120；e2e **22**（任务/通知 21 + 新增文档上传 1，端口改 5174 后全绿）

---

## 1. 为什么

总资产连续三步停在 **1689.1 / 1700 KB（99%）**，余量只剩 10.9 KB：再做一个通知中心的 UI 改动就会顶破预算。这一步只做「腾空间」，不改任何产品行为。

## 2. 做了什么

### 2.1 三处 el-upload → 原生文件选择（−32.4 KB）

`el-upload` 的 JS + CSS 一共 **17.9 + 14.5 = 32.4 KB**，但三处用法都只是「点一下选文件」：

| 位置 | 原用法 | 现做法 |
| --- | --- | --- |
| 文档中心拖拽区（Documents） | `drag` + `auto-upload=false` + `on-change` | `<label>` + 隐藏 `<input type=file multiple>` + `drop` 事件，选完交给原 `onFileChange` |
| 合同审查「上传合同文件」 | `before-upload` 拿到文件 | `<input type=file accept=...>` + `input.click()`，选完调原 `handleContractUpload` |
| 法源「导入资料表」 | `before-upload` 拿到文件 | 同上，选完调原 `handleSourceImport` |

- 三处都没有用 EP 的文件列表、进度条、自动上传与 `before-upload` 返回值，替换后交互一致；
- 文档中心仍支持拖拽（`dragover/drop`），选择后清空 `input.value`，同一文件可再次选择；
- `useDocumentList.onFileChange` 改为接收 `File[]`（原来是 EP 的 `uploadFile.raw` 归一），状态与上传负载不变。

### 2.2 生产 JS 压缩改 terser（−5.4 KB）

`vite.config.js` 增加 `build.minify: 'terser'`（替换 esbuild 压缩）。行为不变，构建时间约 8s → 11s。

### 2.3 e2e 端口固定 5174（修掉一类假失败）

上一轮门禁出现过一次「e2e 全红」：本机 5173 被**另一个项目**（AI 求职助手）的 dev server 占着，Playwright 的 `reuseExistingServer: true` 直接复用了它，用例当然全挂。现在：

- `webServer.command` 改为 `npm run dev -- --port 5174 --strictPort`；
- `reuseExistingServer: false`：端口被占就报错，绝不悄悄复用别人的服务；
- `PLAYWRIGHT_BASE_URL` 仍可覆盖，开发端口仍是 5173。

## 3. 数字

| 项 | 改前 | 改后 | 变化 |
| --- | --- | --- | --- |
| 总资产（raw） | 1689.1 KB | **1651.3 KB** | **−37.8 KB（−2.2%）** |
| 预算余量 | 10.9 KB | **48.7 KB** | +37.8 KB |
| 入口 JS | 84.2 KB | **82.2 KB** | −2.0 KB |
| 入口 gzip | 29.1 KB | **28.4 KB** | −0.7 KB |
| 最大页面 chunk | 85.3 KB | 87.0 KB | +1.7 KB（terser 分块变化，仍 < 130） |
| el-upload 产物 | 17.9 + 14.5 KB | 0 | 整块消失 |

## 4. 验证

- `npm run lint`：0 error（12 条既有 warning 不变）；
- `npm run build` + `bundle:check`：四项预算全绿；
- `npm run test:unit`：120 passed；
- e2e（5174）：任务/通知 21 + 文档上传 1，**22 passed**；
- 新增 `tests/e2e/documents-upload.spec.js`（已进门禁）：拖拽区可见 → 选 2 份 → 「已选 2 份」、上传按钮可用、input 清空；
- 全量门禁：**11/11，exit 0，31.3 分钟**；后端 **2679 passed / 4 skipped**（24 分 22 秒），OpenAPI 376，迁移 123。

## 5. 边界

1. **仍是 97%**：脚本的 95% 预警档还会提示「接近预算」，这次只腾出 48.7 KB，不够再做大页面。
2. **el-upload 语义收窄**：不再有 EP 的文件列表、进度、`before-upload` 返回值与自动上传；三处原本都只当选择器用，行为一致，但以后要进度条得自己实现。
3. **拖入文件不看 accept**：`accept` 只影响选择器（与原 el-upload 一致），拖进来的文件仍按原逻辑校验。
4. **最大 chunk 变大 1.7 KB**：terser 的分块与 esbuild 不同，SystemAnalyticsTabs 87.0 KB，仍远低于 130 KB 预算。
5. **构建变慢**：terser 让 vite build 从 ~8s 到 ~11s，门禁总时长影响可忽略。
6. **e2e 换端口**：CI/本地若依赖 5173 跑 e2e 需改成 5174 或设 `PLAYWRIGHT_PORT`；dev server 本身不变。
7. **没动其它重组件**：el-select/el-popper/el-table 等仍在，进一步压缩需要产品取舍。
8. **新增了一个 e2e**：`documents-upload.spec.js` 只覆盖「选文件→状态更新→按钮可用」，不覆盖真实上传与解析。

## 6. 后续建议

- **通知中心区分「请假代收 / 事件类型代收」**：现在有 48.7 KB 余量，够做这个 UI；
- 若要继续腾量，可评估 `@ctrl/tinycolor`（13 KB，仅 el-button 的 `color` 属性用，当前未使用）与 `el-input-number`（12 KB，仅两处数字输入），但都需要产品确认；
- 把 e2e 端口写进 CI 文档，避免下一个人再踩 5173 的坑。
