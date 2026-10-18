# 第二步交付说明：方向 B 视觉落地（外壳 + 工作台 + 案件工作台 + 咨询 + copilot + 管理页 + 客户门户 + 系统数据 tab）

> 本文是 [2026-10-project-diagnosis.md](./2026-10-project-diagnosis.md) §6「30 天」最后一条（视觉）的落地记录。
> 视觉基准：[ui-preview-direction-b.html](./ui-preview-direction-b.html)（用户指定「前端要改造成的大致样子」）。
> 上一步（右侧面板 PoC）见 [step1-copilot-poc-handoff.md](./step1-copilot-poc-handoff.md)；本次把上一步**明确排除**的整壳改造做了。

## 0. 范围与边界

**做**：全局外壳（左导航 + 顶栏面包屑 + 内容区容器）、工作台首页、案件工作台（案件页头 / 概览 / 案件列表 / 法律咨询 tab）、右侧 copilot 面板四层的视觉；**第二轮补齐侧栏四个一级落点**——文档页、任务页、法律资料（研究）页、通知中心；**第三轮收管理与商业化模块**——审核 tab、系统页（含健康检查 / 反馈 / 任务 / 组织四个 tab）、管理中心、Agent 自动化执行（四个子组件）、计时计费、开发者与安全管理；**第四轮收客户门户与案件工作台剩余 tab**——公开客户门户页、案件门户 tab、关键日期、文书、合同审查、合同台账，并把全站硬编码颜色清零（含 Tailwind 遗留调色板与 `legal-workspace.css` 死类）；**第五轮收系统页 11 个数据密集 tab**——Token 统计、操作日志、告警、实验观测、事件漏斗 / 留存（`PilotAnalyticsTabs` 两个 section）、工具健康、审批台、知识库、敏感治理，口径统一为「筛选条 + 指标卡 + 宽表格」，不引图表库。把方向 B 的版式沉淀成 `design-tokens.css` 里的共享类。

**不做**（仍是旧版式，属下一次范围）：copilot 的两处如实降级（过程时间线仍是事后回放、逐句溯源仍是整篇粒度）——那是后端补 trace / 句级引用映射的活，不是视觉问题；信息架构层面的「文档 tab 与合同台账 tab 内容重复」见 §4.7。

## 1. 交付物

| 文件 | 说明 |
| --- | --- |
| `frontend/src/styles/design-tokens.css` | 方向 B 令牌与共享版式类：`.app-card*`、`.app-page-head*`、`.app-crumbs*`、`.app-tag`、`.app-stat*`、`.app-thread*`、`.app-rail*`、`.app-timeline*`、`.app-workspace-body`、`.workspace-primary`、`.app-question-bubble`、`.app-answer-*`、`.app-evidence*`、`.app-verify-badge` |
| `frontend/src/App.vue` | 顶栏面包屑（取代原 section-strip 大标题）、导航分组标签与选中态、248px 侧栏、品牌渐变标识、1280px 内容容器 |
| `frontend/src/composables/workspaceBreadcrumb.js` | 新增：页面 → 外壳的面包屑登记（`setWorkspaceCrumbs` / `clearWorkspaceCrumbs` / `useWorkspaceCrumbs`） |
| `frontend/src/views/LegalWorkspace.vue` | 案件 / 法律资料 / 审核三个视图的页头、概览卡片化（主栏 + 300px 右栏）、案件卡片网格、咨询 tab 标题去重 |
| `frontend/src/components/legal/CaseHeader.vue` | 案件页头改为方向 B（标题 + 状态胶囊 + 继续处理；案情摘要不再重复，归「案件概览 · 案情摘要」卡片） |
| `frontend/src/components/legal/LegalConsultationsTab.vue` | 咨询模块：卡片化提问区、结果卡（问题气泡 / 结论与建议 / 已知事实 / 待补充事实 / 证据行 + 核验徽标）、历史咨询 |
| `frontend/src/components/legal/WorkbenchHome.vue` | 工作台首页：页头 + 4 张数据卡 + 待处理审核 / 最近案件 / 常用入口卡片 |
| `frontend/src/views/Documents.vue` | 文档页：页头（保留 `.page-header` e2e 锚点）+ 4 张数据卡 + 上传卡片 + 案件上下文面包屑 |
| `frontend/src/views/Tasks.vue` | 任务页：页头（范围筛选 / 看板-表格切换 / 新建任务）+ 4 张数据卡 + 任务清单卡片（看板泳道与表格同一张卡内） |
| `frontend/src/components/legal/LegalSourcesTab.vue` | 法律资料（研究）页：检索卡 + 资料目录 rail 卡 + 阅读器卡（头部挂 `.app-card-head`）+ 折叠维护区三卡 |
| `frontend/src/views/Notifications.vue` | 通知中心：页头 + 通知卡片（工具条与列表收进 `.app-card`，未读行主色竖条 + 浅底） |
| `frontend/src/components/documents/DocumentWorkspace.vue` | 顺带修两个真实版式缺陷：卡片头标题被按钮挤成竖排、摘要瓦片固定 3 等在窄栏挤成竖排 |
| `frontend/src/components/CopilotDock.vue`、`components/copilot/*` | copilot 面板四层的方向 B 版式（问题气泡、处理过程时间线、答案块、证据卡 + 已核验/待核验徽标、动作 chips）；`CopilotAnswer.vue` 里补上「展开为整页」深链 |
| `frontend/src/components/legal/LegalReviewTab.vue` | 审核 tab（第三轮）：页头（人工审核 kicker + 待处理审核 + SLA 指标）+ 审核流程条 + 队列卡 / 详情卡双栏；顺带修三个真缺陷（分配下拉被挤成竖排标签、`catch (error)` 引用了不存在的 ref、队列项缺 flex 布局） |
| `frontend/src/views/System.vue`、`components/system/SystemOverviewBar.vue`、`system/SystemHealthTab.vue`、`system/SystemFeedbackTab.vue`、`system/SystemTasksTab.vue`、`system/SystemOrgTab.vue` | 系统页（第三轮）：页头 + 概览条（4 数据卡 + 3 胶囊）+ 浅色 tab 栏（38px 高、13.5px、2px 选中条）；健康检查 / 反馈 / 任务 / 组织四个 tab 卡片化 |
| `frontend/src/views/ManagementCenter.vue` | 管理中心（第三轮）：页头 + 「平台运行 / 自动化与质量」两张卡（组内为可点行，带描述与箭头） |
| `frontend/src/components/agent/*` | Agent 自动化执行（第三轮）：页头 + 3 数据卡 + 执行口径卡（指标 chips）+ 一句话目标卡；`.input-card` 锚点保留 |
| `frontend/src/views/LegalBilling.vue` | 计时计费（第三轮）：4 数据卡 + 5 张 `.app-card` 分区（计时器 / 工时 / 费用通知单 / 账单 / 回款）+ 详情弹窗键值网格 |
| `frontend/src/views/LegalDeveloper.vue` | 开发者与安全管理（第三轮）：页头 + 运营概览 6 数据卡 + 开发者应用卡（表单/密钥提示/应用表原样保留） |
| `frontend/src/views/LegalPortal.vue` | 公开客户门户（第四轮）：品牌页头（34px 渐变标识）+ 进展时间线 + 公开文档表 + 账单数据卡；只读页，全部 `.app-card` 化 |
| `frontend/src/components/legal/LegalPortalTab.vue` | 案件门户 tab（第四轮）：4 张门户数据卡 + 品牌配置卡 + 门户链接表卡 + 访问分析卡（组件不自带页头，页头在 `LegalWorkspace.vue`） |
| `frontend/src/components/legal/LegalDeadlines.vue` | 关键日期（第四轮）：创建期限卡（2×2 表单网格）+ 期限列表卡（类型带分类圆点、状态胶囊、操作列） |
| `frontend/src/components/legal/LegalDraftsTab.vue`、`legal/LegalContractTab.vue`、`views/LegalContracts.vue` | 文书 / 合同审查 / 合同台账（第四轮）：文书编辑器 + 文书结构 rail；合同审查录入态与结果态页头（自带，外层不再加）、审查摘要卡、原文/风险双栏卡、三个折叠区；合同台账列表卡 |
| `frontend/src/styles/tailwind.css` | 第四轮清理：删除与令牌冲突的 `@theme` 调色板（`#F7F8FA`/`#3B5BDB`/`#E3E5E9`）与无人使用的 `.ui-panel`/`.ui-section-*`，只留 Chat.vue 仍在用的工具类与三个圆角变量；文件头注明「颜色只有 `design-tokens.css` 一个来源」 |
| `frontend/src/styles/legal-workspace.css` | 第四轮清理：删掉已无引用的 `.legal-section*` / `.legal-page-heading` / `.legal-toolbar` / `.legal-list-row` / `.legal-divider`，只保留仍是 e2e 锚点的 `.legal-matter-tab*` 与 `.legal-empty` |
| `frontend/src/views/LegalWorkspace.vue`（第四轮） | 案件 tab 页头统一：文档 / 文书 / 关键日期 / 合同台账 / 客户门户 / 计时计费 / 案件工作记录各自 `.app-page-head`；合同审查与审核 tab 因组件自带页头而**不加**外层标题（避免两个 h1/h2） |
| 全站取色清理（第四轮） | 11 个组件/页面里的 33 处硬编码色（`#F7F8FA`/`#293542`/`#8793A0`/`#FFF5D9` 等）换成令牌；`main.js` 运行时错误面板改走 `--color-danger*`；新增 `--color-highlight-bg/-mark/-border` 三个文稿高亮令牌 |
| `docs/review/step2-*.png`、`step3-*.png`、`step4-*.png`、`step5-*.png`、`step6-*.png` | 三十一张实拍截图：step2 八张（工作台首页 / 案件列表 / 咨询结果卡 / copilot 答复 / 文档 / 任务 / 法律资料 / 通知中心）、step3 四张（系统 / 管理中心 / Agent / 审核）、step4 七张（案件概览 / 客户门户 tab / 关键日期 / 文书 / 合同审查 / 合同台账 / 公开客户门户页）、step5 十一张（系统页 11 个数据 tab 的真后端整页实拍）、step6 一张（法源详情弹窗） |
| `frontend/src/components/system/SystemTokensTab.vue` 等 10 个系统 tab 组件 | 第五轮：Token 统计（503→615 行）/ 操作日志 / 告警 / 实验观测 / 事件漏斗 / `PilotAnalyticsTabs`（漏斗 + 留存两个 section）/ 工具健康 / 审批台 / 知识库 / 敏感治理统一为「筛选条 + 指标卡 + 宽表格」口径；`el-card`/`el-statistic`/`el-descriptions`/`el-row`/`el-col` 归零，数值列统一 `.app-num`，表统一包 `.app-table-wrap` |
| `frontend/src/styles/design-tokens.css`（第五轮补充） | 数据密集页口径类：`.app-filter-bar`（+`.app-filter-label`/`.app-filter-bar-actions`）、`.app-table-wrap`、`.app-num`、`.app-metric-grid-2/3`、`.app-card-body.is-flush`；≤1100px 时指标网格统一塌成单列 |
| `frontend/src/components/legal/SourceDetailDialog.vue` | **新增**：法源详情弹窗宿主（此前 `useLegalSourceDetail` 的共享状态没有任何组件渲染，是 step1 记录的既有缺陷）；挂在 `views/LegalWorkspace.vue`，`el-dialog` 需显式 import（本项目没有全局注册） |
| `frontend/src/utils/references.js`（+ `utils/__tests__/references.test.js`） | **新增**：`asReferenceList()` 过滤模型产出的 `null` / 空对象引用条目，避免一个坏条目让整块结果卡渲染报错；落到咨询结果卡、文书法律依据、审核证据区、文档分析归一化四处 |
| `frontend/tests/e2e/verify-system-real.spec.js` | **新增**（集成用例，默认 skip）：真后端逐 tab 走查系统页 15 个 tab，断言无 `#runtime-error-panel`、无 pageerror、无 5xx，并可选整页实拍（`E2E_SHOT_DIR`） |
| `frontend/tests/e2e/verify-u2-u4.spec.js`（第五轮升级） | 集成用例不再只对着写死的 mock 断言：引用条目改为从真实法源库解析（`resolveConsultReference`），断言标题 / 版本 / 施行日期 / 条文首段，法源库缺条文时用例直接报错而不是静默通过；案件上下文按真实案件 id 进入咨询 tab |
| `frontend/vite.config.js`（第五轮修复） | 增加 `server.warmup.clientFiles`（App / Agent / 任务 / 系统 / 文档 / 案件工作台 / 通知 / copilot），把重型路由的按需转换成本移出用例计时——此前全量 e2e 每次有 2~4 条 `page.goto` 30s 超时的偶发失败（失败用例每次不同、热跑必过），修复后同批 spec 冷启动 8 passed / 19.9s |
| `scripts/seed_visual_check_samples.py` | **新增**（视觉核查用样例数据脚本，所有文本带「视觉核查样例」标注，文件头注释给出逐表清理 SQL）：让操作日志 / 工具健康 / 告警 / 审批 / 知识库 / 敏感治理等 tab 在真后端下也有真实数据密度可拍 |

## 2. 关键设计决策

1. **共享类进令牌文件，组件不各写一份**：所有卡片面、标签、数据块、列表项、证据行的边框/圆角/阴影/间距都在 `design-tokens.css` 定义一次；组件只做排布。改一处即全站生效。
2. **渐变只留一处**：文件里原本写着「品牌渐变已移除（渐变底 + 彩色光晕是模板感来源）」。本次只开一个例外——`--gradient-brand-mark`，仅用于 ≤36px 的品牌标识方块（侧栏 logo、助手头像），不铺面、不加光晕；其余位置继续禁止渐变。
3. **面包屑由页面登记**：`router-view` 之间传不了 props，而「张三劳动合同纠纷」这类标题只有页面自己（从接口）知道，因此用模块级 ref 做单向登记；路由一变外壳先清空，避免上一页标题残留。
4. **不为了像预览而造数据**：咨询 tab **没有**照搬预览右侧的「相关法条 / 风险点 / 案件时间线」栏——法条已经以证据行形式出现在结果卡里（重复），风险点/时间线在本 tab 拿不到可信数据源（案件活动在概览 tab，期限在关键日期 tab）。宁可少一块，也不放看起来像真的假面板。
5. **不为了 4px 改测试**：预览的收起态是 56px，`copilot-dock.spec.js:113` 断言 ≤52px；改回 52px，测试不动。
6. **「展开为整页」落到案件工作台而不是 `/chat`**：整页要看的是**这一条咨询的完整结果卡**（结论 / 事实 / 证据 / 送审入口），而不是一个空聊天框；因此深链 `/legal-workspace?case_id=…&tab=consultation&consultation_id=…`，复用通知/案件记录已经在用的 `consultation_id` 恢复链路（`LegalWorkspace.vue` 的 `consultationFocusId` → `LegalConsultationsTab` 的 `initialConsultationId`）。
7. **数据密集的管理 tab 不硬套卡片**（第三轮）：系统页只统一了页头 / 概览条 / tab 栏与四个偏流程的 tab；Token 统计、事件漏斗、实验观测等 11 个 tab 是宽表格 + 图表，套卡片只会更挤，留给单独一轮定表格与图表口径。
8. **能改样式就不动组件**（第三轮）：系统页的 `el-tabs`、开发者应用的表单/表格、计费详情弹窗都保留 Element 组件，只把它们的皮肤拉回方向 B（高度、字号、选中条、圆角、色板走令牌）。理由是这些组件的交互（懒加载 tab、表单校验、弹窗挂载点）被 e2e 依赖，换掉是纯风险、零收益。
9. **颜色只留一个来源**（第四轮）：Tailwind 的 `@theme` 调色板与 `design-tokens.css` 定义的是**两套**色值（`#F7F8FA` vs `#F5F7F9`、`#3B5BDB` vs `#4F46E5`），虽然设计令牌因未分层而实际胜出，但两份定义并存迟早出事；扫全仓确认没有任何组件用 Tailwind 的颜色工具类后，直接删掉调色板，只留 Chat.vue 依赖的布局/字号工具类。
10. **页头归属一次说清**（第四轮）：合同审查、律师审核两个 tab 的组件**自带** `.app-page-head`（它们的标题随状态变化，例如「开始合同审查」→ 合同名），因此 `LegalWorkspace.vue` 的外层**不加**标题，并在该处留注释；其余 tab（文档 / 文书 / 关键日期 / 合同台账 / 客户门户 / 计时计费 / 工作记录）由外层统一给页头。这条约定避免了「两个大标题」，也是本轮唯一需要口头对齐的结构约束。
11. **不为截图造数据，但为截图修 mock**（第四轮）：文书 tab 第一次实拍出现 `TypeError: rows.filter is not a function`——原因是我的截图 mock 把 `GET /legal/drafts` 写成了 `{items,total}`，而真实接口返回**裸数组**（`app/services/legal/legal_workspace_service.py:930 -> list[dict]`，与 `/legal/review-queue` 同款）。修 mock 后正常；顺手给截图用例加了「出现 `#runtime-error-panel` 即失败」的断言，让 mock 形状错误立刻暴露而不是产出错图。
12. **数据密集 tab 的口径 = 表格 + 指标 + 筛选条，不引图表库**（第五轮）：先扫全仓确认没有任何图表依赖（echarts / canvas 命中 0），11 个 tab 的真实内容也都是宽表格与清单（Token 统计 / 操作日志 / 告警 / 实验 / 漏斗 / 留存 / 工具健康 / 审批 / 知识库 / 敏感治理）。因此统一为：筛选条 `.app-filter-bar` 进 `.app-card-body`，指标用 `.app-stat` 网格，表格保留 `el-table` 并包 `.app-table-wrap`，数值列加 `.app-num`（等宽数字）。**不**为「看起来像仪表盘」而引入图表库或造图。
13. **模型产出的引用先过滤再渲染**（第五轮）：集成用例暴露出一个真缺陷——`references` 里只要有一个 `null` 条目，`v-for` 里取 `r.source_id` 就抛错，整个结果卡白屏（`LegalConsultationsTab.vue`）。这条数据来自模型 JSON，形状不可控，因此新增 `utils/references.js` 的 `asReferenceList()` 统一过滤（并补 3 条单测），在咨询 / 文书 / 审核 / 文档分析四处渲染前调用。
14. **引用依据弹窗补上宿主**（第五轮）：`useLegalSourceDetail` 的共享状态此前**没有任何组件渲染**（step1 §5 已记录），点引用什么都不发生。本轮补 `SourceDetailDialog.vue` 挂在案件工作台；同时给单例加 `surface` 参数（`legal` 默认 / `copilot`），避免 copilot 的内联展开与工作台弹窗同时触发。踩到的坑：本项目 `el-dialog` 没有全局注册，必须显式 `import { ElDialog } from 'element-plus/es/components/dialog/index'` + 样式，否则控制台只报「Failed to resolve component」，弹窗静默不出现。
15. **集成用例对「真实数据链」断言，而不是对 mock 断言**（第五轮）：U-2 原本断言写死的 `中华人民共和国劳动合同法 / 版本 2025-01-01`，其实那只是 mock 里的字符串，等于自己验自己。改为运行时从真实法源库解析一条带条文的法源（`resolveConsultReference`），再断言弹窗里出现该法源的标题 / 版本 / 施行日期与条文首段；法源库为空时用例**直接失败**并提示先跑 `scripts/import_legal_sources.py`，避免静默通过。
16. **把「服务器慢」与「产品坏」分开**（第五轮）：全量 e2e 的偶发失败（`page.goto` 30s 超时、失败用例每次不同、热跑必过）经定位是 Vite 冷启动按需转换重型路由的耗时，不是被测代码缺陷。因此**不**加 retry 掩盖，也不删断言，而是：`frontend/vite.config.js` 里 `server.warmup.clientFiles` 预热重型入口（App / 登录 / Agent / 任务 / 系统 / 文档 / 案件工作台 / 通知 / copilot），`frontend/playwright.config.js` 把 `timeout` 放到 60s、`navigationTimeout` 放到 45s 并写明原因。修复后冷启动同批 spec 8 passed / 19.9s，全量 41 passed / 0 failed。

## 3. 验收证据（均在本机复现）

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 静态检查 | `npx eslint src --ext .vue,.js` | 0 error / 13 warning（全部为既有 `vue/attributes-order`，比改造前 48 条少 35 条） |
| 单元测试 | `npm run test:unit` | **61 passed / 0 failed**（含新增 `utils/references` 3 条） |
| 构建 | `npm run build` | **exit 0**（11.25s；入口 `dist/assets/index-CNSQBe2W.js` **145.83 kB raw / 51.00 kB gzip**，System 分包 117.91 kB / 31.57 kB gzip，案件工作台分包 101.19 kB） |
| 组件级 e2e | `npx playwright test --workers=1`（`E2E_RUN_INTEGRATION=true`） | **41 passed / 0 failed（1.9m）**——含 4 条真后端集成用例；不带 `E2E_RUN_INTEGRATION` 时为 37 passed / 4 skipped / 0 failed |
| 真后端集成：系统页 | `E2E_RUN_INTEGRATION=true E2E_ADMIN_USERNAME=admin E2E_ADMIN_PASSWORD=… npx playwright test verify-system-real --workers=1` | **1 passed**：15 个 tab 逐个走查，0 个 `#runtime-error-panel`、0 pageerror、0 个 5xx |
| 真后端集成：咨询信任链 | `E2E_RUN_INTEGRATION=true npx playwright test verify-u2-u4 --workers=1` | **3 passed**：置信度标签、引用条目（标题 / 版本 / 当前有效）、法源详情弹窗读到**真实条文**、反馈 score=1 / -1 载荷 |
| 模块页专项 | `frontend-engineering` / `contract-upload` / `tasks-flow` / `system-feedback-tasks` / `system-orgs` / `sources-flow` / `invoice-lifecycle` / `billing-contract` / `pricing-checkout` / `agent-flow` / `u3-workflow` / `pilot-workflow` / `portal-flow` / `contracts-flow` / `draft-export` / `chat-stream` / `quota-429` / `developer-apps` / `billing-contract` | 全部 passed（0 failed） |
| copilot 契约 | `npx playwright test copilot-dock --workers=1` | **9 passed** |
| 取色硬编码 | 全仓正则扫描（排除三个样式入口文件） | clean：`design-tokens.css` 是唯一颜色来源；`element-overrides.css` 只做 Element 变量映射；`tailwind.css` 已无调色板 |
| 视觉实拍 | 1440×900 截图 | 7 张 mock 实拍（案件概览 / 客户门户 tab / 关键日期 / 文书 / 合同审查 / 合同台账 + 公开客户门户页）+ 15 张真后端整页实拍（系统页 15 个 tab，其中 11 个数据 tab 归档为 `step5-*.png`）+ 1 张法源详情弹窗，全部无失败请求、无 `#runtime-error-panel` |
| 「展开为整页」深链 | 一次性验证用例（验完已删） | 点击后 URL 带 `tab=consultation&consultation_id=77`，发出 `GET /legal/consultations/77`，结果卡按 id 自动恢复，无运行时报错 |

> 过程中修掉一个真缺陷：改造期间 copilot 答复组件里混入了一行非法标记（`<acp …>`），会让 Babel/ESLint 解析失败并直接挂掉 `npm run build`；已删除。

> 稳定性观察（如实记录）：全量 e2e 曾多次出现 2~4 条 `page.goto` 30s 超时的偶发失败（失败用例每次不同：`agent-flow` / `tasks-flow` / `system-orgs` / `copilot-dock` / `quota-429`，热跑必过）。第五轮定位为**冷启动时 Vite 按需转换重型路由模块图超时**，不是被测代码问题；修法见 §2 决策 16（`vite.config.js` 的 `server.warmup` + `playwright.config.js` 放宽 `timeout`/`navigationTimeout`）。修复后同批 spec 冷启动 8 passed / 19.9s，全量（含集成）**41 passed / 0 failed / 1.9m**。

## 4. 未验证面 / 开口项（如实标注）

1. ~~**`npm run bundle:check` 仍红**~~ **已解决（2026-10-18，step10）**：入口 raw **102.0/120**、gzip **35.8/42**、最大页面 chunk 121.8/130、总资产 **1579.0/1600**，四项全绿。手法＝清掉 32 个文件的 element-plus 桶导入、删零使用的全局注册、把最后两处 `el-row/el-col` 换原生栅格；预算红线未放宽。详见 `docs/review/step10-bundle-budget.md`。
2. **真后端集成用例需要本机后端与管理员凭据**：`verify-system-real.spec.js` 依赖 `E2E_ADMIN_USERNAME` / `E2E_ADMIN_PASSWORD`，`verify-u2-u4.spec.js` 依赖 `demo_lawyer` 与法源库里有条文的法源（脚本会显式失败并提示先跑 `scripts/import_legal_sources.py`）。两者都需 `E2E_RUN_INTEGRATION=true`，CI 里必须注入这些变量，否则默认跳过（跳过是刻意设计，避免在无后端环境误报绿）。
3. **copilot 面板 ↔ 真实后端仍未在同一次会话联通过**（沿用 step1 的口径）：本轮打通的是「案件工作台咨询 tab ↔ 真后端」，copilot 侧仍只跑 mock 契约（9 条）。
4. **仍未改造**：无（第一~五轮的页面收敛已覆盖到系统页 15 个 tab）。剩下的旧版式只在两个「故意保留」处：系统组织 tab 的三张表单卡仍是 `el-card`（e2e 锚点）、数据密集 tab 的宽表格保持 `el-table`（决策 12）。
5. **窄栏下的两处已知瑕疵**（不裁切、不影响功能）：法律资料页阅读器状态行在主栏 ~300px 时会折成两行；任务页表格 9 列固定宽 750px 超过卡片可用宽 705px，需横向滚动才见「操作」列（改动前即如此）。
6. **实拍截图的局限**：案件 tab 与客户门户的 7 张 mock 实拍（空列表居多）只能证明版式，真实数据密度未验证；系统页 15 张走的是真后端 + 样例数据（见 §4.10）。`role` 必须是 `admin` 才看得到管理中心分组（`dept_admin` 会被路由守卫拦成 403，`frontend/src/auth/capabilities.js` 是能力字典的单一来源）。
7. **案件工作台「文档」tab 里同时渲染了案件材料与合同台账**（与「合同台账」tab 内容重复）：核对 `git show HEAD:frontend/src/views/LegalWorkspace.vue` 确认是**改造前既有**的信息架构，不是本轮引入；没有擅自删（删掉会同时动到 e2e 与用户预期），留给信息架构一轮统一决定。
8. **客户门户链接表格「有效期」列偏窄**：`2026-11-05 00:00` 会折成两行（`LegalPortalTab.vue` 的列宽）；功能正常，属窄列折行，未在截图后回改。
9. **过程事故（已修，如实记录）**：第四轮清理 `tailwind.css` 时，注释里写了 `mt-*/text-*` 这样的字面量，其中的 `*/` 提前闭合了 CSS 注释，Tailwind 解析报 `Invalid declaration` 并弹出 Vite 错误遮罩，整站白屏——被公开门户截图用例当场抓到，改注释后恢复。教训：CSS 注释里不要出现 `*/` 序列。
10. **库里有为视觉核查写入的样例数据，需要时可清理**：脚本 `scripts/seed_visual_check_samples.py`（所有文本都带「视觉核查样例」字样，脚本头注释给出逐表清理 SQL）写入了 5 条 `tool_call_logs` 挪期 + 1 次 error、2 个 `knowledge_bases`、2 条 `agent_approval_requests`(pending)、1 条 `agent_runs`(error)、3 条 `operation_logs`(module='async_task' 且 action 以 `_failed` 结尾)、2 条 `email_send_requests`(failed)；另外 24 条带「视觉核查样例」的 operation_logs 走的是产品接口 `POST /api/analytics/oplogs`；法源库补了《中华人民共和国劳动合同法》17 条条文（source id 71，属 demo_lawyer）。这些数据**留在库里**，方便复跑截图与集成用例；要清就按脚本头注释执行。
11. **e2e 冷启动 flake 已定位并修复（如实记录过程）**：全量跑多次出现 `page.goto` 30s 超时，失败用例每次不同（agent-flow / tasks-flow / system-orgs / copilot-dock / quota-429），热跑必过（agent-flow 首个用例冷 27.4s vs 热 1.3s）。根因是 Vite 按需转换重型路由模块图的耗时超过用例默认超时，**不是**被测代码的问题。修法：`frontend/vite.config.js` 增加 `server.warmup.clientFiles`（App / 登录 / Agent / 任务 / 系统 / 文档 / 案件工作台 / 通知 / copilot），把转换成本移到 dev server 启动期，同时 `frontend/playwright.config.js` 把 `timeout` 放宽到 60s、`navigationTimeout` 到 45s（断言强度不变）；修复后同三条 spec 冷启动 **8 passed / 19.9s**（首个用例 1.0s），全量含集成 **41 passed / 0 failed / 1.9m**。
12. **引用详情弹窗只挂在案件工作台**：`SourceDetailDialog.vue` 挂在 `frontend/src/views/LegalWorkspace.vue`；copilot 内仍走内联展开（`openSourceDetail(ref, 'copilot')`），两处不会同时弹。其他页面（文档分析、任务等）若也要「点引用看原文」，需要各自挂一次该组件。
13. **法源库/知识库的内容覆盖仍是演示级**：本轮只导入了一部法律的 17 条条文用于验证信任链；`/api/documents/knowledge-bases`、`sensitivity_level=confidential` 等查询在本机数据下为空，相关 tab 的空态版式已实拍，但满数据版式未验证。

## 5. 下一步建议（按性价比）

1. ~~决策打包预算（降 bundle / 重新标定），否则 CI 一直红。~~ **已完成（2026-10-18，step10）**：降体积四项全绿，CI 门禁恢复；红线未放宽。下一步若要继续压总资产，从 `el-table` 样式按需化入手（见 `docs/review/step10-bundle-budget.md` §5）。
2. 把两条集成用例接进 CI：起 MySQL + Redis + 后端，注入 `E2E_RUN_INTEGRATION=true`、`E2E_ADMIN_USERNAME` / `E2E_ADMIN_PASSWORD`、以及 `demo_lawyer` 凭据与一部带条文的法源；否则「引用详情读到真实条文」这条信任链没有回归保护。
3. 信息架构一轮：决定案件工作台「文档」tab 与「合同台账」tab 的重复内容如何合并（见 §4.7）。
4. copilot 的「展开为整页」已接通（深链回案件工作台咨询 tab 并恢复结果卡）；剩下的「入口收敛」是 Chat 入口归一与 agent 入口对非管理员可见，属产品决策，未自行放开。
5. 内容侧：把「法源库有条文」从演示级补到可用级（本轮只导了劳动合同法 17 条），这是「逐句溯源 / 忠实度」两层体验能否说清楚的前提。
