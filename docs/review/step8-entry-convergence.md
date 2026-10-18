# 步骤 8：助手入口收敛（Chat 并入 copilot · Agent 入口对非管理员开放）

对应 `docs/review/2026-10-project-diagnosis.md` 的 30 天项「入口收敛」：
> Chat 并入 copilot、保留"展开整页"深度视图；agent 入口对非管理员可见。**验收**：三入口归一，普通用户也能用到助手。

## 0. 做了什么 / 不做什么

**做**：
1. **三入口归一**：工作台首页「案件助手」快捷入口、案件工作台「打开助手」、导航开关，全部改为**打开右侧助手面板**，不再离开当前页面跳 `/chat`。
2. **保留深度视图**：面板头部新增「整页」入口（带当前 `case_id` 进 `/chat`）；`CopilotAnswer` 的逐答案「展开为整页」保持原行为（进案件咨询 tab 的完整结果卡）。
3. **Agent 入口对非管理员开放**：能力表放开 + 导航项移入「工作区」组；系统级入口（系统 / 管理中心 / 评测与审计 / 计费）仍 adminOnly。

**不做**（明确边界，避免把产品改动混进工程改动）：
- 不改 `/chat` 的问答引擎与 WS 协议（面板走 REST 咨询、`/chat` 走 WS 流式，**两条链路的统一不在本步范围**）。
- 不删 `/chat` 路由（它是保留的深度视图；`frontend/tests/e2e/chat-stream.spec.js` 直接访问它，仍然有效）。
- 不动后端权限模型（`/agent/*` 的权限本来就是 `get_current_user` + `user_id` 隔离，见下）。
- 不改面板的问答链路、四层可解释性与 trace 渲染。

## 1. 交付物

| 文件 | 变更 |
| --- | --- |
| `frontend/src/composables/useCopilotDock.js` | 新增。面板开合的单例状态（模块作用域 `ref`）+ `openCopilot/closeCopilot/toggleCopilot` |
| `frontend/src/App.vue` | 面板开合改由 `useCopilotDock()` 托管（`@toggle="toggleCopilot"`）；Agent 导航项从「管理中心」组移入「工作区」组、去掉 `adminOnly` |
| `frontend/src/views/LegalWorkspace.vue` | 概览侧栏「打开助手」与 `@open-chat` 改调 `openCopilot()`（不再 `router.push('/chat')`） |
| `frontend/src/components/CopilotDock.vue` | 头部新增「整页」按钮（`openFullPage` → `/chat`，有案件时带 `case_id`）+ `.cop-fullpage` 样式 |
| `frontend/src/auth/capabilities.js` | `AGENT_VIEW/AGENT_RUN/AGENT_CANCEL` 移入 `USER_CAPS`（含理由注释） |
| `frontend/src/auth/__tests__/capabilities.test.js` | 契约更新：user 拥有 Agent 能力，但没有 `SYSTEM_VIEW`/`ORG_MANAGE` |
| `frontend/src/composables/__tests__/useCopilotDock.test.js` | 新增：开/关/切换 + 多调用点共享同一份状态 |
| `frontend/tests/e2e/entry-convergence.spec.js` | 新增回归 4 例（两个入口开面板不跳页 / 整页带 `case_id` / 普通用户 Agent 入口可见可进） |
| `frontend/tests/e2e/frontend-engineering.spec.js:62` | 旧契约同步：非管理员「导航无 Agent」→「导航有 Agent」 |

截图：`docs/review/step8-converge-workbench.png`、`step8-converge-dock.png`、`step8-converge-agent-user.png`（真实后端 + 普通用户账号 `demo_lawyer`）。

## 2. 关键决策

1. **面板是唯一入口，`/chat` 降级为深度视图**。三个入口原来都 `router.push('/chat')`——用户点「打开助手」会被带离当前页面，丢掉正在看的案件上下文。收敛后入口只做一件事：展开右侧面板；要看整页再从面板头部进。
2. **用单例 composable，而不是事件总线或 provide/inject**。面板挂在 `App.vue`，业务页面在路由视图内部（provide 需跨 `router-view` 传递），事件总线要额外的解绑与竞态处理。一个模块作用域的 `ref` 最简单，且可单测（`useCopilotDock.test.js` 断言「两个调用点共享同一状态」）。
3. **Agent 入口只改前端，后端一行不动**。先读码确认：`app/api/agent/agent_api.py` 全部端点只用 `get_current_user`，运行/日志/检查点/审批都按 `user_id=current_user.id` 过滤；唯一的管理员判断是 `app/api/agent/mcp_api.py:72`（`sql_query_tool` 仅 admin）。也就是说后端早就是「普通用户可用、只看自己的运行」，管理员门槛纯粹是前端导航 + 能力表造成的。改动因此收敛为：能力表移三项 + 导航换组。
4. **旧契约测试要跟着产品改**。`frontend/tests/e2e/frontend-engineering.spec.js:62` 原来断言「非管理员导航里没有 Agent」——那正是被推翻的旧规则。改测试而不是改回产品，并在注释里写明变更日期与理由。
5. **「整页」放在面板头部，而不是每个答案里**。逐答案的「展开为整页」语义是「把这条咨询结果展开到案件工作台」，与「助手整体换到整页」不是一回事；两者并存，各自指向正确的目标。

## 3. 验收

| 项 | 结果 |
| --- | --- |
| `npx eslint src --ext .vue,.js` | 0 error / 13 warning（均为既有 `vue/attributes-order`） |
| `npm run test:unit` | 68 passed（原 66，+2 为 `useCopilotDock`） |
| `npm run build` | exit 0（入口 `dist/assets/index-DX0hUOGg.js` 146.21 kB raw / 51.16 gzip） |
| `npx playwright test --workers=1` | **43 passed / 5 skipped / 0 failed**（5 条 skipped 是需真实后端的集成用例，由 `E2E_RUN_INTEGRATION` 控制） |
| `E2E_RUN_INTEGRATION=true npx playwright test --workers=1` | **48 passed / 0 failed（2.6m）**——含 5 条真后端集成用例（系统页 15 tab 走查 / trace 读回 / U-2、U-4×2） |
| 真后端 · 普通用户可用性 | `demo_lawyer`（role=user，org 15）：`GET /api/agent/registry`、`/agent/runs`、`/agent/metrics`、`/agent/approvals` **全 200** |
| 真后端 · 页面实拍 | 普通用户视角：导航含 Agent、工作台含「案件助手」入口、面板头部含「整页」；进入 `/agent` 无 403、无运行时报错 |

### 3.1 回归用例

`frontend/tests/e2e/entry-convergence.spec.js`（4 passed）：
1. 工作台「案件助手」快捷入口 → 面板展开、URL 仍在 `/legal-workspace`；
2. 案件工作台「打开助手」 → 面板展开、URL 仍带 `case_id=3`、上下文条显示「案件 #3」；
3. 面板「整页」 → `/chat?case_id=3`；
4. 普通用户（role=user） → 导航有 Agent、进入 `/agent` 无 `.forbidden-state`。

## 4. 开口项与边界

1. **两条助手链路仍未统一**：面板走 `POST /legal/consultations`（REST，含 trace 四层），`/chat` 走 `WS /api/ws/chat`（流式）。本步只统一了**入口**；把面板问答切到流式、或让 `/chat` 复用咨询链路，属于 90 天项「copilot 扩到文书审查 + 草稿」的邻近工作。
2. **移动端导航**取前 6 项（`App.vue` `mobileNavItems`），不含 Agent；窄屏用户要进 Agent 需从桌面端导航或直接访问 `/agent`。
3. **Agent 页面的管理员语义未变**：`mcp_api.py:72` 仍拦 `sql_query_tool`；普通用户在 Agent 页若触发该工具会得到后端的拒绝，前端未做额外提示。
4. `capabilities.js` 仍是前端本地映射（文件头已注明「前端权限仅用于 UX 控制，不是安全边界」），后端下发 capabilities 的改造未做。
