# 第五十四步交付说明：LegalWorkspace 主 tab 按需拆 chunk

> 一句话：合同审查、文书、审核三个最大的主 tab 从 LegalWorkspace 入口 chunk 拆成独立异步 chunk。工作台页面 chunk **117.1 → 36.9 KB**，最大页面 chunk 降到 **85.3 KB**（SystemAnalyticsTabs），总资产 **1672.7 → 1678.8 KB**，仍在 1700 预算内（余 21.2 KB）。

- 日期：2026-10-28
- 前端：`views/LegalWorkspace.vue`
- 测试：工作台相关 e2e 11 条全过；门禁见 §5
- 无后端/数据库改动

---

## 1. 为什么继续拆

step53 之后总资产回到 1672.7 / 1700 KB，但最大页面 chunk 仍是 **LegalWorkspace 117.1 KB**。原因是三个最大的案件主 tab 仍是静态 import：

| 组件 | 拆出的 chunk |
| --- | --- |
| `LegalContractTab` | 34.8 KB JS + 18.3 KB CSS |
| `LegalDraftsTab` | 19.9 KB JS |
| `LegalReviewTab` | 22.3 KB JS + 11.8 KB CSS |

它们只在对应 tab 激活时才需要，却跟着 `/legal-workspace` 首屏一起下载；概览、案件列表、文档等路径都被这三块拖着。

## 2. 改法

`LegalWorkspace.vue`：

```js
const AsyncLegalContractTab = defineAsyncComponent(() => import('../components/legal/LegalContractTab.vue'))
const AsyncLegalDraftsTab = defineAsyncComponent(() => import('../components/legal/LegalDraftsTab.vue'))
const AsyncLegalReviewTab = defineAsyncComponent(() => import('../components/legal/LegalReviewTab.vue'))
```

模板里三处替换为 `AsyncLegalContractTab` / `AsyncLegalDraftsTab` / `AsyncLegalReviewTab`：

- 合同审查 tab（`tab=contract`）；
- 文书 tab（`tab=draft`）；
- 审核 tab（`tab=review`，以及全局 `view=review` 入口）。

`ref` 保留：

- `contractsTabRef` / `draftsTabRef` / `reviewTabRef` 仍指向异步组件；
- tab 已挂载时 `prefill()` / `refresh()` 照常可用；
- tab 未挂载时 ref 为 `null`，调用仍是 no-op——与拆前静态组件的行为一致（拆前未挂载同样没有实例）。本次**没有改变** prefill 时序，也没有修这个既有问题，见边界 2。

## 3. 结果

| 指标 | 拆前 | 拆后 | 预算 |
| --- | --- | --- | --- |
| 入口 JS raw | 80.2 KB | **80.3 KB** | 100 |
| LegalWorkspace 页面 chunk | 117.1 KB | **36.9 KB** | — |
| LegalContractTab | 页内 | **34.8 KB** | — |
| LegalDraftsTab | 页内 | **19.9 KB** | — |
| LegalReviewTab | 页内 | **22.3 KB** | — |
| 最大页面 chunk | 117.1 KB | **85.3 KB**（SystemAnalyticsTabs） | 130 |
| 总资产 raw | 1672.7 KB | **1678.8 KB** | 1700 |
| 总资产余量 | 27.3 KB | **21.2 KB** | — |

说明：总资产增加 **6.1 KB**，是三个异步 chunk 的包装/共享边界开销；换来 LegalWorkspace 首屏少下 80 KB，仍在预算内。

## 4. 验证

工作台相关 e2e **11 passed / 32.8s**：

- `billing-contract.spec.js`：计时计费契约；
- `contract-upload.spec.js`：合同上传触发 multipart 审查；
- `contracts-flow.spec.js`：台账列表→新建→版本→Diff→节点→签署；
- `draft-export.spec.js`：文书生成→导出 docx；
- `pilot-workflow.spec.js`：登录→合同审查→关键日期→门户发布；
- `tasks-flow.spec.js`（3 条）：任务看板、共享任务、深链；
- `copilot-case-context.spec.js`（3 条）：合同/文书/咨询 tab 的上下文参数。

覆盖了三个新异步 tab 的首次加载与交互；`contracts-flow` 也覆盖台账在文档 tab 下的复用。

全量门禁见 §6。

## 5. 门禁

- `scripts/check_all.py`：**10/10，17.5 分钟，exit 0**（本树最终状态）。
- 后端全量：**2457 passed / 4 skipped**；OpenAPI **361**；前端单测 **96 passed**。
- 包体：入口 **80.3 / 100 KB**、最大页面 chunk **85.3 / 130 KB**、总资产 **1678.8 / 1700 KB**。

## 6. 边界

1. **三个 tab 各自 chunk**：首次点合同审查/文书/审核会多一次网络请求；之后命中缓存。
2. **prefill 时序未修**：`handleGoToDraftFromConsult` / `handleGoToReviewFromConsult` 仍在 `setMatterTab` 前调用 ref；tab 未挂载时 prefill 为 no-op。这是拆前就存在的问题，本次只搬加载方式，不改变行为；要修需改成「待挂载后应用」的显式状态（后续单开）。
3. **ref 类型**：异步组件的 ref 在解析完成后才指向内部实例；父组件不要在挂载前假定有 `prefill/refresh`。
4. **总资产略增**：三个 chunk 共增加 6.1 KB；如果继续拆更多 tab，先量总量，别把 21.2 KB 余量吃光。
5. **最大页面 chunk 换人**：现在最大是 `SystemAnalyticsTabs` 85.3 KB，不再是 LegalWorkspace；后续若继续治理，应看 System 观测组。
6. **无接口/权限变化**：只改组件加载方式；tab 名称、URL query、review 入口、合同/文书接口都不变。
7. **回滚**：恢复三个静态 import 与模板标签即可；总资产会回到 1672.7 KB，LegalWorkspace 回到 117.1 KB。

## 7. 后续建议

- 余量 21.2 KB，足够再加一个小功能或一次小拆分，但不够大改；
- 下一个包体目标是 `SystemAnalyticsTabs`（85.3 KB），可再按「实验/投递/模板」拆，但要先量 chunk 开销；
- 若继续做咨询闭环，优先修 prefill 时序（边界 2），它比继续拆包更影响用户感知。
