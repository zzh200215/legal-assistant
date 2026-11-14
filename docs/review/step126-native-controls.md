# 第一百二十六步交付说明：包体治理（原生数字输入 / 开关 / 间距）

> 一句话：把只用了几处的 `el-input-number`、`el-switch`、`el-space` 换成本地的 `NumberField`、`AppSwitch` 与 flex 容器，总资产 **1659.0 → 1638.5 KB（−20.5 KB）**，预算从 98% 回到 **96%**。

- 日期：2026-11-14
- 前端：`components/NumberField.vue`、`components/AppSwitch.vue`、`utils/numberField.js`（+单测）、`views/LegalBilling.vue`、`components/agent/AgentCommandCenter.vue`、`components/documents/DocumentSidebar.vue`、`components/documents/DocumentWorkspace.vue`、`components/legal/LegalPortalTab.vue`、`components/system/SystemTasksTab.vue`、`tests/e2e/billing-contract.spec.js`
- 后端：无改动
- 契约：OpenAPI **377 paths / 178 schemas / 28 error codes**（无接口变化）
- 迁移：**无新增**（仍 124 个，head `20261114_0123`）
- 测试：前端单测 124 → **129**；e2e 24（计费用例扩展）；后端 2709 不变

---

## 1. 为什么

通知中心连续五步吃掉了约 8 KB，总资产到 98%（余量 41 KB），再加两个 UI 功能就会顶到 98.5% 以上。这次不再加功能，只换掉三个「只为几处 UI 引入整套组件」的依赖。

| 组件 | 用量 | 产物 |
| --- | --- | --- |
| `el-input-number` | 6 处（计费 5 + Agent 1） | JS 7.6 KB + 样式 |
| `el-switch` | 3 处（文档受控下载 2 + 门户聚合 1） | JS 5.1 KB + 样式 |
| `el-space` | 2 处（文档工具条、System 任务行） | JS 2.5 KB + 样式 |

## 2. 做了什么

### 2.1 `NumberField`（数字输入）

- 基于已在包里的 `el-input`，`inputmode="decimal"`，保留 `min / max / precision / size / disabled / placeholder`；
- 输入过程中只回传能解析的数值，**失焦或回车时**才按范围夹取并补齐小数位——避免用户输入「1440」时中间态被截成 144；
- 解析/归一/格式化抽成 `utils/numberField.js` 三个纯函数，配 5 条单测覆盖空值、半截输入、越界、取整。

### 2.2 `AppSwitch`（开关）

- 原生 `<button role="switch" aria-checked>` + 自制轨道/滑块，支持 `v-model` 与 `change` 两种用法；
- `loading` 只做禁用 + `aria-busy`（EP 的旋转图标省掉，调用方本来就有「保存中」语义）；
- 替换 3 处：文档「允许下载 / 下载水印」、门户「聚合全部」。

### 2.3 flex 容器（间距）

- 两处 `el-space` 改成 `<div>` + `flex; gap: 8px`；同时清掉 EP 相邻按钮的 12px 外边距，视觉间距与原来一致。

## 3. 数字

| 项 | 改前 | 改后 | 变化 |
| --- | --- | --- | --- |
| 总资产（raw） | 1659.0 KB | **1638.5 KB** | **−20.5 KB** |
| 预算占比 | 98% | **96%** | −2pt |
| 预算余量 | 41.0 KB | **61.5 KB** | +20.5 KB |
| 入口 JS | 82.3 KB | **82.2 KB** | −0.1 |
| 入口 gzip | 28.4 KB | **28.4 KB** | 0 |
| 最大页面 chunk | 87.0 KB | **87.0 KB** | 0 |
| 前端单测 | 124 | **129** | +5 |

三个组件 chunk（7.6 + 5.1 + 2.5 = 15.2 KB JS）整块消失，另减去随组件带入的样式与胶水约 5.3 KB。

## 4. 验证

- 单测：`utils/numberField.test.js` 5 条全过（解析/整取/越界/精度/显示）；
- e2e：
  - `billing-contract.spec.js` 增加手动录入断言——时长填 `2000` 失焦后变 `1440`，提交载荷 `duration_minutes: 1440` 且是数字；
  - 另外跑了 20 个涉及这些页面的既有 spec（合同、文书、门户、System、计价、配额等）**34 条全过**；
- 前端 lint 0 error（12 条既有 warning）；
- 包体：四项预算全绿，总量回到 **96%**；
- 全量门禁：**11/11，exit 0，17.4 分钟**；后端 **2709 passed / 4 skipped**（15 分 43 秒），前端单测 129、e2e 24、OpenAPI 377、迁移 124 全绿。

## 5. 边界

1. **不再有步进按钮**：`el-input-number` 的 +/− 按钮没了，键盘上下键与滚轮由浏览器原生处理；移动端靠数字键盘。
2. **精度只在失焦时归一**：输入过程中保留用户原文（如 `90.456`），失焦后显示 `90.46`；提交时用的是失焦后的值。
3. **开关没有加载动画**：`loading` 只是禁用 + `aria-busy`，没有转圈图标。
4. **`el-space` 的间距规则简化**：统一 8px，没有 EP 的 `fill / alignment / spacer` 等参数；当前两处都只需要换行 + 8px。
5. **可访问性靠原生语义**：`role="switch"` + `aria-checked`，但没有实现左右方向键切换（EP 有）。
6. **仍是 96%**：余量 61.5 KB，够再做一到两个中小 UI；继续加之前仍建议先量。

## 6. 后续建议

- **通知中心的筛选状态写进 URL**（`?delegated_from=16&days=7&status=unread`）：刷新/分享/后退都能保留上下文，纯前端约 +2 KB；
- 若还想腾空间，可评估 `el-radio-group`（5.3 KB，20 处引用但多是单选按钮组）与 `el-alert`（2.1 KB），不过收益已明显变小；
- 提醒自己：新页面优先复用 `NumberField` / `AppSwitch`，不要再为单个控件引入整套 EP 组件。
