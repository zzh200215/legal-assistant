# 前端视图拆分 · 剩余工作计划（续接手册）

> 本文档记录前端「上帝视图拆分 + Pinia 状态上收」已完成部分与剩余部分的**精确策略**，
> 供后续会话/开发者按既有模式继续，避免重复分析。当前所有改动均 `npm run build` + `npm run lint` 绿灯。

## 1. 已完成（可直接复用为模板）

| 产物 | 类型 | 说明 |
|---|---|---|
| `src/stores/auth.js`（`useAuthStore`） | Pinia | `user/currentUser/isAdmin` + `loadMe/setUser/clear`，覆盖 App/Login/Tasks/System |
| `src/stores/app.js`（`useAppStore`） | Pinia | 应用级种子 store |
| `src/components/tasks/TaskCreateDialogs.vue` | 组件 | Tasks 三弹窗，`ref` 暴露 `open*()` + `@refresh` |
| `src/components/tasks/TaskDetailDialog.vue` | 组件 | 任务详情弹窗，子数据加载 + `reloadSubTasks` 暴露 |
| `src/components/legal/LegalSourcesTab.vue` | 组件 | 法源管理 tab，自包含（无 caseId） |
| `src/components/legal/LegalConsultationsTab.vue` | 组件 | 法律咨询 tab，`go-to-draft`/`go-to-review` 事件 |
| `src/components/legal/LegalDraftsTab.vue` | 组件 | 文书草稿 tab，`prefill` 暴露 |
| `src/components/legal/LegalContractTab.vue` | 组件 | 合同审查 tab，`prefill` 暴露 |
| `src/components/legal/CaseCreateDialog.vue` | 组件 | 新建案件弹窗，`orgId` prop + `created` emit |
| `src/composables/useLegalSourceDetail.js` | composable | 法源核对状态/动作/`verificationTagType`（模块级单例） |
| `src/composables/useQuota.js` | composable | `quotaSummary/loadQuota/quotaHint`（模块级单例） |
| `src/composables/useDocuments.js` | composable | 文档知识库共享状态/动作（模块级单例） |
| `src/composables/useAgentWorkbench.js` | composable | Agent 工作台共享状态/动作（模块级单例，含 socket） |
| `src/components/documents/DocumentSidebar.vue` | 组件 | Documents 左资源栏 |
| `src/components/documents/DocumentWorkspace.vue` | 组件 | Documents 右工作区 |
| `src/components/agent/AgentHeader.vue` | 组件 | Agent 页头/概览/专家目录/命令条 |
| `src/components/agent/AgentCommandCenter.vue` | 组件 | Agent 目标输入/预览/敏感确认弹窗 |
| `src/components/agent/AgentExecutionView.vue` | 组件 | Agent 执行结果/编排/产出/详情 |
| `src/components/agent/AgentSidePanels.vue` | 组件 | Agent 审批/历史 |

> **进度更新（2026）**：`LegalWorkspace.vue`（1549→702）、`Tasks.vue`（1010→719）、
> `System.vue`（2039→183）、`Documents.vue`（1976→220）、`Agent.vue`（1657→59）已全部拆分完成。

**关键模式**：抽组件时（a）把业务逻辑整体迁入子组件并内聚其 composable；（b）跨 tab 共享依赖先抽 composable（已做 `useLegalSourceDetail`/`useQuota`）；（c）作用域样式需随组件复制。

## 2. 剩余工作与精确策略

### 2.1 LegalWorkspace.vue 的 consultation/contract/draft 三 tab

已完成的共享模块：`useQuota`、`useLegalSourceDetail`（法源核对）。剩余跨 tab 依赖需事件化：

- **`goToReviewFromConsult` / `goToDraftFromConsult`**：父函数改写 `draftForm/contractForm/activeTab`（还依赖 `CATEGORY_TO_DRAFT_TYPE`、`categoryLabel`）。拆法：tab 发 `emit('go-to-review', consultResult)` / `emit('go-to-draft', consultResult)`，父侧 handler 接收 `consultResult` 参数。
- **`caseId`**（`currentCaseId`）、**`onReviewSubmitted`**（`() => reviewTabRef.value?.refresh()`）：作为 prop 传入 tab。
- **`submitConsultFeedback`/`submitReviewFeedback`/`submitDraftFeedback`**：依赖各 tab 的 result + 父的 `submitFeedback` 通用包装（`legalWorkspace.submit*Feedback`）。拆法：在子组件内直接调对应 `legalWorkspace.submit*Feedback`，不再复用父的 `submitFeedback`。
- 每个 tab 的展示辅助（`riskTagType/riskLabel/confidenceTagType/categoryLabel/sourceStatusType/sourceStatusLabel/statusTagType/statusLabel`）从 `useLegalWorkspacePresentation` 直接具名导入。

### 2.2 Tasks.vue 详情弹窗

详情弹窗（~120 行模板）与看板/表格共享 `changeStatus/decompose`（留在父）。拆法：
`TaskDetailDialog.vue` 接收 `task/visible/canEdit/subTasks/comments/taskLogs/relatedAgentRuns/…` 作为 props，`v-model:visible` + `change-status`/`decompose`/`collab-update`/`comment-submit`/`open-source`/`open-agent-run` 事件；子数据加载（`loadSubTasks/loadComments/loadTaskLogs/loadRelatedAgentRuns`）由子组件 watch `task` prop 触发。

### 2.3 System.vue（✅ 已完成，1863→183）

`System.vue` 已拆为「壳 + 概览条 + 12 个 tab 子组件」，全部落于 `src/components/system/`：

- `SystemOverviewBar.vue`：顶部运行/失败/重试/成功率概览 + 命令条（读 `useSystemTaskMonitor`/`useSystemApprovals` 单例）。
- `SystemHealthTab` / `SystemTokensTab` / `SystemOplogsTab` / `SystemAlertsTab` / `SystemExperimentsTab` / `SystemFeedbackTab` / `SystemToolHealthTab` / `SystemApprovalsTab` / `SystemKnowledgeTab` / `SystemOrgTab` / `SystemSensitivityTab` / `SystemTasksTab`：各自内聚其 composable + 展示辅助函数 + 弹窗（任务详情/反馈处理/问答回放已 `append-to-body` 移入对应 tab）。
- 父 `System.vue` 仅保留 `activeTab` + 路由 watch + `openKnowledgeDocument` 跳转 + `el-tabs` 壳。

**共享状态上收（模块级单例）**：`tokenDays` 抽到 `useSystemPeriod.js`；`useSystemTaskMonitor`/`useSystemApprovals`/`useSystemActivity` 改为模块级单例（顶部概览、任务重试后刷新告警、Token tab 的 Agent 成功率卡片跨 tab 复用同一份状态）；`useAuthStore.loadMe` 改为模块级 Promise 记忆化，多子组件并发 `await` 只发一次 `/auth/me`。

### 2.4 Documents.vue（已拆分，1976→220）/ Agent.vue（已拆分，1657→59）

两视图均为「深度共享状态 + 单流程编排」，拆分采用**模块级单例 composable** 承载全部共享状态与动作，子组件各自内聚展示与操作：

- `src/composables/useDocuments.js`：文档知识库全量状态 + 数据动作（列表/筛选/分页/上传/下载策略/对比/分析轮询/问答反馈）模块级单例；QA 反馈状态随主单例承载，供 `selectDocument`/`uploadAndAnalyze` 复位。
- `src/components/documents/DocumentSidebar.vue`：左资源栏（知识库/筛选/检索参数/当前文档下载策略/多文档对比/文档列表+分页）。
- `src/components/documents/DocumentWorkspace.vue`：右工作区（工具栏/问答/关联 Agent/解析版本问答面板/分析结果/对比结果/任务产出）。
- `src/views/Documents.vue`：仅剩页头上传 + 概览指标 + `DocumentSidebar`/`DocumentWorkspace` + 路由 watch/生命周期。

- `src/composables/useAgentWorkbench.js`：Agent 工作台全量状态 + 动作（socket 实时执行/计划预览/历史/审批/指标/注册表/演示上下文/取消/导航）模块级单例。
- `src/components/agent/AgentHeader.vue`：页头 + 概览条 + 专家目录 + 命令条。
- `src/components/agent/AgentCommandCenter.vue`：目标输入 + 演示卡 + 计划预览 + 敏感操作确认弹窗（`append-to-body`）。
- `src/components/agent/AgentExecutionView.vue`：执行结果 + 步骤时间线 + Supervisor 编排 + 产出对象 + 执行详情。
- `src/components/agent/AgentSidePanels.vue`：待审批 + 运行历史（含分页）。
- `src/views/Agent.vue`：仅剩四个子组件 + 路由 watch/生命周期 + socket 清理。

**共享状态上收（模块级单例）**：Documents 与 Agent 均以模块级单例跨子组件共享同一份状态（含 socket 连接、轮询定时器），避免双实例分叉；与此前 `useSystemTaskMonitor`/`useApprovals` 模式一致。

## 3. 验收

每步：`cd frontend && npm run build && npm run lint`（仅允许既有告警 `vue/multi-word-component-names` 单字视图名、`vue/attributes-order`；新增代码须 0 error）。后端不受影响，无需跑 pytest。
