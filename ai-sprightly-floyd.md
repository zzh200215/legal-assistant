# 律智检（AI 法律助手）多视角优化分析

## Context（背景）

律智检是一款面向中小律所、企业法务、独立顾问的 AI 法律协作 SaaS。当前 V2.0 能力已交付（法律检索、合同审查、法律咨询、文书生成、评测基准、多租户/审批流/订阅计费），正在执行 V3.0 路线图 Phase 11（律师工具：计时计费/案件日历/客户门户），按里程碑 **2026-08 启动 10 家律所封闭内测**，12 月商业化发布。

本分析以**内部团队评审/迭代规划**为目的，从**产品经理、AI 应用技术专家、用户、工程稳健与安全合规、商业化与市场**五个视角展开，落到**可执行清单**粒度。结论基于对 `app/`、`frontend/`、`eval/`、PRD_V3.0.md、V3.0待完成任务清单.md 的实际探索。

---

## 一、总体判断（先说结论）

**这个项目的技术/AI 架构远超产品验证进度，最大的风险不是技术，而是"8 月试点在验证错误的东西，或什么都验证不出来"。**

- 架构上：40 个 API 模块、约 130 张表、自研 LLM 客户端 + Agentic RAG + 监督-工人多 Agent + MCP + 评测体系，工程底子很强，甚至**偏重**。
- 产品上：三线业务（咨询/审查/文书）+ 办公自动化（会议/邮件/日历/任务/Agent）+ 开放平台 + 签署 + 计费 + 门户，**范围过载**，与已验证的需求不匹配。
- 商业化上：有定价、有里程碑，但**没有埋点漏斗、没有单位经济模型、没有销售动作**，"8% 转化率"是假设而非目标。
- 结论：优化优先级 = **① 让试点能量化验证 → ② 收缩范围到律师工作流主线 → ③ 补齐阻断企业信任的 P0 安全/生产项 → ④ 用律师审核反馈回流 AI 质量 → ⑤ 打磨前端信任与引导**。

---

## 二、产品经理视角

### 现状问题

1. **范围过载（最核心的产品问题）**。`frontend/src/views/` 存在 12 个无路由的孤儿页（Emails/Inbox/Meetings/Calendar/DataAnalysis/Workflows/Prompts/Schedules/Archive/Outbound/DocumentAssistant），属早期 "ai-office" 残留；Agent 编排、MCP、办公自动化与法律主线并存。产品叙事不清：到底卖给谁、解决哪个岗位的哪件事。
2. **PMF 未验证就先铺开**。PRD 目标用户三层（独立律师/律所/企业法务），但 V3.0 之前没有真实用户反馈循环。试点（8 月，10 家律所）是第一次真实验证，但**当前产品没有任何埋点漏斗**，管理员看板只统计用户/订阅/收入，无法回答"用户进来后哪一步流失"。
3. **主线工作流尚未闭环**。V3.0待完成任务清单把计时计费、客户门户、电子签署、开放 API 均列为 P0"待闭环/外部前置"——即对律师最核心的"办案+计费+对客交付"链路当前并不完整，试点前必须收敛。
4. **差异化叙事偏虚**。竞品对比（无讼/Alpha/理脉/威科）成立，但"AI 原生+全流程"这个差异点对独立律师太宽。试点需要有 1-2 个"最优场景"（如劳动仲裁 + 民间借贷），而不是全面覆盖。

### 优化动作（产品）

- **P-1 试点前装好漏斗埋点**：事件流覆盖「注册 → 首次咨询 → 首次审查 → 首次文书 → 首次审核通过 → 升级付费」，配合现有管理看板，试点周报直接看数据。**【已完成 2026-08-02：/api/admin/funnel 从既有业务表推导六阶漏斗（注册→首次咨询→首次审查→首次文书→首次审核通过→升级付费），可追溯历史数据无需额外埋点表；System.vue 新增「用户漏斗」tab，展示每跳转化率/占注册比例/相对条形图 + 注册→首次咨询平均激活时长。修复：生产库无 subscription_plans/user_subscriptions 表（计费走 legal_billing 组织维度），升级付费阶做双轨回退——有订阅表按订阅，无则按用户所在组织是否存在已确认收款/已付发票推导，`cohort.billing_source` 标注口径来源】**
- **P-2 收缩叙事到一人一场景**：试点只主打"独立律师办案 + 计费闭环"；办公自动化（会议/邮件/任务/Agent）对律师角色隐藏或收进"高级功能"。**【已完成 2026-08-02：App.vue 主导航对非管理员（律师）角色隐藏「待办任务」「Agent配置」「系统中心」，只保留法律工作台/法律知识库/对话记录；办公自动化页面本无路由，导航已收敛】**
- **P-3 清理孤儿页面与死代码**：12 个无路由页面 + 对应 API 模块要么上线要么下架，避免维护拖累和用户困惑。**【已完成 2026-08-02：删除 12 个孤儿页（Emails/Inbox/Meetings/Calendar/DataAnalysis/Workflows/Prompts/Schedules/Archive/Outbound/DocumentAssistant/Dashboard）+ 10 个死前端 api wrapper（email/meeting/calendar/workflow/prompt/schedule/archive/outbound/mailbox/documentAssistant），清理 api/index.js；前端 vite build 通过，无残留引用】**
- **P-3b 后端办公死代码下架**（2026-08-02 跟进）：评估后确认 calendar/archive/business_workflow/data_analysis/document_assistant 为纯办公孤立模块，无法律复用——整体删除其 API/服务/模型/专属测试（约 21 文件），并移除 agent 注册表与权限矩阵中的 sales_daily_report_tool、清理 agent 提示词/审批风险表引用。**例外保留**：calendar 模型（CalendarSuggestion）被法律门户「期限→日历建议」端点复用，属法律功能，予以保留；analytics_api.py 为混合模块，仅删除其数据分析端点（/data-sources、/data-reports），保留 LLM 观测/告警/反馈端点（前端 System.vue 在用）。前端修复 Agent.vue/Tasks.vue/useSystemTaskMonitor 对已下架 /emails /meetings 页面的悬空跳转，并复活 Tasks.vue「生成同步邮件」（其前端 wrapper 在 P-3 被误删，方法已补回 task.js，后端 /emails/from-tasks 端点存活）。重新生成 openapi 快照（293 paths）。全量测试 549 passed（较删除前 560 恰减被删的 11 个用例）。】**
- **P-4 试点定义为"验证假设的实验"**：明确 2-3 个可证伪假设（如"律所愿为计费+门户付费"、"合同审查是留存之王"），用周报口径回答，而非"把功能做完"。
- **P-5 定义北极星 + 留存**：北极星建议定为"每周完成 ≥1 次 AI 辅助法律任务的有活跃案件律师数"；补 7 日/30 日留存看板。

---

## 三、AI 应用技术专家视角

### 现状亮点（应保持）

- 自研 httpx LLM 客户端 + 双模型路由（qwen-plus 主 / qwen-turbo 小），`legal_*` 强制走主模型；流式首块后禁切模型保证流完整。
- Agentic RAG（plan→retrieve→assess→refine）+ 混合检索（词法+稠密+RRF）+ Neo4j 图证据有界提升 + 自动降级，检索链路质量好。
- Prompt 已 DB 化 + 灰度（`prompt_service.py` SHA-256 用户桶）。
- 强制免责声明、禁预测胜诉、PII 脱敏、高风险进律师审核队列、拒答机制——法律安全的正确骨架。
- 评测体系 8 个脚本（Hit@K / 引用正确率 / 拒答准确率 / 答案准确率 / 延迟）。

### 现状问题

1. **评测数据是"假数据"**。`eval/results.md` 的 32 题样例集是 3 份 Markdown 演示文档；"87.5% pass rate"基于演示数据。真实法律语料的检索 top_k、置信阈值（全局 0.35）都没有经过验证。
2. **最高杠杆的数据被浪费**：律师审核队列的「通过/退回补充事实/转线下」决策是**黄金标注数据**，但当前没有回流到评测集或微调数据管线。
3. **Prompt 双轨**：`legal_service.py`（864 行）里法律 Prompt 是 Python 常量，而系统已有 DB 化 `prompt_service`——法律 Prompt 未纳入版本化灰度，无法 A/B。
4. **幻觉防线是"结构性的"而非"内容性的"**：有结构化 JSON 约束和拒答，但没有针对"引用了错误法条/失效法条"的内容级校验。Neo4j 有 AMENDS/AMENDED_BY，但前端不展示法条版本/效力状态，引用正确性无从核验。
5. **无成本视角**：`token_usage`/`llm_call_logs` 有数据，但没有"每次咨询/审查/文书成本多少"的分析，定价没有单位经济支撑（见商业化视角）。
6. **确定性兜底与 LLM 路径双轨**：`legal_service.py` 的规则兜底与 LLM 输出逻辑并存，存在行为漂移风险。

### 优化动作（AI）

- **AI-1 评测"去演示化"**：用脱敏真实语料构建冻结分层评测集（P1-08 已列），规模扩到 100+ 题、覆盖三线业务 + 拒答 + 法条版本失效案例；纳入 CI 回归门禁，模型/Prompt/解析器变更必须出报告。
- **AI-2 建立审核反馈回流闭环（最高杠杆）**：律师的通过/退回/转线下决策 + 批注 → 定期抽取为新增评测用例；达到阈值后可做微调数据。这让 AI 质量随使用量自我提升。**【已完成 2026-08-04：闭环全线打通——`scripts/export_review_feedback.py`（审核决策 → 评测用例 JSONL，增量游标 + 同目标去重，回流用例带 document_type/category 复现字段）→ `eval/load_review_feedback.py`（按律师决策推断结构性回归断言：approve=防退化、return=必须标注缺失事实、offline=风险≥medium）→ `run_generation_eval.py --review-feedback`（默认读 eval/review_feedback_eval.jsonl，回归用例计入报告 regression 层，CI eval-regression 自动消费）。`eval/review_feedback_eval.jsonl` 已入库 12 条模拟首周试点用例（咨询/审查/文书各 4 条×approve/return/offline），确定性路径 119 题全过（其中回流回归 12/12）。测试：test_review_feedback_export.py（6）+ test_review_feedback_eval.py（6）新增；修复预存不稳定测试 test_legal_reference_verification（原依赖真实 LLM 输出，改为 mock 确定性路径）。全量 605 passed。】**
- **AI-3 法律 Prompt 迁入 prompt_service**：从 `legal_service.py` 常量迁到版本化模板，开启灰度/A/B。**【已完成 2026-08-03~04：迁移 0055 同步 5 个模板（legal_consultation/legal_contract_review/legal_draft_generation/legal_followup/legal_contract_compare，基线 app/services/prompt_defaults.py）；legal_service.py 五处 LLM 调用已全部改为 prompt_service.render_by_name 渲染（08-04 复核无硬编码 prompt 残留），灰度/A-B 由 prompt_service 按 user_id 桶生效。相关测试 47 项通过。】**
- **AI-4 内容级引用核验**：答案中的法条引用要带"法源 + 效力状态 + 修订版本"；命中失效/被修订法条时标注并提示当前有效版本（复用 Neo4j AMENDS）。这是法律 AI 的核心信任点。
- **AI-5 成本按动作核算**：建"每次咨询/审查/文书 ≈ 模型 × token 成本"报表，支撑定价与配额。**【已完成 2026-08-02：llm-billing/stats 增加 by_action 成本聚合；已产出 M-1 真实单价】**
- **AI-6 评估模型策略**：在评测集上对比 qwen-plus 与更强模型（qwen-max 等）+ 提示词变体，用数据决定高价值场景是否值得换更贵模型；评估 JSON Schema 结构化输出强制程度是否已最大化。

---

## 四、用户视角

### 现状问题

1. **首屏过载**：登录后进入 10 个 tab 的 `LegalWorkspace.vue`（1354 行单文件）。有角色化引导页 `legal-onboarding`，但**不是默认落地页**，用户一来就要面对全功能矩阵。
2. **信任透传不足**：咨询结果有法源标题+条款，但**不能点击核对原文/效力状态/修订版本**；风险等级 + "提交律师审核"存在，但升级路径不够显眼。法律场景用户最需要"我凭什么信你"。
3. **移动端基本不可用**：<760px 时导航隐藏。免费版个人咨询（5 次/月）是获客渠道，个人用户大概率走手机/微信，当前体验断裂。
4. **无端侧质量反馈**：文档问答有反馈，但咨询/审查/文书输出上没有统一的"回答是否有用 + 原因"反馈入口，既伤用户信任也丢回流数据。
5. **模块割裂**：咨询/审查/文书/审核各自为政，缺少"从咨询结论一键进入审查/文书/审核"的工作流串联。

### 优化动作（用户）

- **U-1 把角色化引导设为默认落地页**（复用 `legal-onboarding`）：独立律师先做"首个案件 + 首次计费"，企业法务先做"首份合同审查"，10 分钟出第一个成果。**【已完成 2026-08-02：路由 `/` 重定向到 `/legal-onboarding`，登录后默认进入角色化引导页；引导页新增「进入工作台 →」CTA 与更清晰的布局，三步清单按角色（独立律师/律所管理员/企业法务）展示，保存进度后进入 /legal-workspace】**
- **U-2 每个 AI 输出加"信任三件套"**：置信度 / 可点击核对的引用来源（含法条版本与效力状态）/ 明确下一步动作（提交审核、导出、下载）。
- **U-3 咨询→审查→文书→审核 一键流转**：把工作流串起来，形成"办案主循环"。**【已完成 2026-08-03：咨询结果页新增「进入合同审查」「生成文书」按钮——按咨询分类预选文书类型、带入已知事实、切 tab 即续写；工作台顶部新增「当前案件」选择器 + 新建案件 dialog，咨询/审查/文书创建时统一归档到所选 case_id（后端三 create + followup 透传 case_id 并做同组织归属校验）；三子组件 tab 改 lazy 挂载。案件 item_counts 直接支撑 M-2「单案件闭环」验证口径】**
- **U-4 端侧质量反馈**：所有 AI 输出加 👍/👎 + 原因选择，直接进 AI-2 回流管线。
- **U-5 收敛导航**：对律师角色隐藏办公自动化入口，降低认知负担；10-tab 工作台改为上下文感知或分组渐进展示。
- **U-6 移动端至少保障个人咨询流程**：这是免费版获客通道，堵着等于放弃漏斗入口。

---

## 五、工程稳健与安全合规视角

### 现状亮点

- 安全骨架扎实：AES-256-GCM 加密敏感字段、LLM 前 PII 脱敏、资源不存在统一 404 防枚举、严格案件成员撤销即时生效、Webhook HMAC、审计+操作日志双轨、登录锁定。
- 测试量大（80+ 测试文件，151 passed），授权测试已覆盖核心链路。

### 现状问题（多条直接阻断商业发布）

1. **前端在生产容器里跑 Vite dev server**（`frontend/Dockerfile` 跑 `vite dev`）——不是静态构建产物，这是 SaaS 发布级问题；compose 里也没看到 TLS/反代层。
2. **V3.0 清单已自我认定的 P0 缺口**：生产迁移/灾备未在真实 MySQL 上演练（测试用 SQLite 内存库）；`LEGAL_DATA_ENCRYPTION_KEY` 未独立管理、可能从 `SECRET_KEY` 派生；电子签署是"假流程"（手填 `provider_request_id`）；开放 API 异步任务卡在 `queued`。
3. **API 契约无门禁**：P0-02 明确列出前后端 DTO 漂移点，靠人工对齐，无 OpenAPI 快照 diff 防线。
4. **巨型单文件**：`LegalWorkspace.vue`(1354行)、`Documents.vue`(2365行)、`agent_service.py`(116KB)、`document_service.py`(76KB)、`legal_service.py`(864行)，可维护性风险。
5. **可观测性缺前端与链路**：有 LLM 日志/用量/运营看板，但无前端错误上报（只有 dev 错误面板）、无 API↔Celery 全链路 tracing，试点事故难定位。
6. **无 CI 管线**：lint + 测试 + 评测回归 + 契约检查没有统一 CI 门禁，靠人工跑。

### 优化动作（工程）

- **E-1 前端改生产构建**：Vite build → nginx 静态托管 + gzip/缓存，dev server 只留开发环境。**【已完成 2026-08-02：nginx.conf + 多阶段 Dockerfile + compose 8080 静态托管】**
- **E-2 试点前关门 P0 安全/生产项**：独立加密密钥管理 + 轮换（对照 P0-07）；在真实 MySQL 做 0042–0048 升级/回滚/恢复演练，实测 RTO≤30min / RPO≤4h；关闭未就绪的签署/开放 API 开关而不是留着半成品。**【部分完成 2026-08-02：独立密钥、真实 MySQL 演练、关闭半成品开关已做；待办：密钥轮换、RTO/RPO 正式实测】** → **【2026-08-04 全部完成：DR 演练实测（备份 2s/恢复 3s、94 表行数一致，记录 docs/dr-drill-record.md）；密钥轮换隔离库完整实测；生产库 aibg 首加密已执行——5 行明文（legal_contract_reviews.content×3、legal_drafts.content×2）全部加密（enc:v2），单密钥模式 verify 5/5 可解密、0 失败，.env 无需改动（LEGAL_DATA_ENCRYPTION_KEY 即激活密钥，密文内嵌版本号）】**
- **E-3 建立 OpenAPI 契约门禁**：后端生成 openapi.json 快照，前端共享 schema 或 CI diff，杜绝 DTO 漂移（P0-02）。**【已完成 2026-08-02：docs/openapi-snapshot.json + check_openapi_contract.py diff 门禁】**
- **E-4 拆巨型文件**：至少把 10-tab 工作台按 tab 拆成路由级组件；服务层按模块拆分。
- **E-5 上真实监控**：Sentry（前端错误）+ OpenTelemetry（API/Celery 链路），配告警；试点日志分级脱敏策略落地。
- **E-6 建 CI**：GitHub Actions：pytest + lint + 评测回归（AI-1）+ 契约检查（E-3），合并即门禁。
- **E-7 基础压测**：对咨询/审查/检索三条主路径做小规模压测，确保 10 家律所试点不崩。**【已完成 2026-08-02：scripts/loadtest_legal_paths.py + 基线数据；发现 async 端点同步 DB 调用拖垮并发，修复建议见 E-7 结论】**
  - **【2026-08-04 修复闭环（E-7 结论落地）】**：① 根因一——async 端点内同步 DB 调用阻塞事件循环：`legal_api.py` 九个 LLM 相关端点（consultations/followup/contract-reviews/resubmit/upload/compare/drafts/resubmit/article-search/hybrid-retrieval-test）改为 sync def + `asyncio.run` 包装（FastAPI 线程池执行，事件循环不阻塞）；② 根因二——LLM 等待 2-5s 期间请求持有 MySQL 连接，默认池 5+10 在高并发下耗尽导致 30s 超时：服务层 6 处（consultation/followup/contract-review/resubmit×2/draft/resubmit-draft）+ 检索 1 处在 await LLM 前 `expunge/物化 + commit/rollback` 归还连接；检索改为纯 dict 物化不依赖 ORM 生命周期；连接池默认调至 pool_size=20/max_overflow=40（`DATABASE_POOL_SIZE/MAX_OVERFLOW` 可配）；③ 修复压测脚本 RPS 统计 bug（原用 sum(latencies) 当墙钟，并发下严重低估吞吐，实际此前 "4 RPS" 实为 ~36 RPS）；④ conftest.py 隔离 CHROMA_PERSIST_DIR，避免存量 chroma 数据 schema 冲突。**修复后基线（LLM 模拟 50ms，并发 10）：咨询 36.5 RPS/p50 243ms、审查 34.0 RPS/p50 248ms、检索 160.7 RPS/p50 54ms、0 错误；并发 20 无超时（修复前 12.5% 错误 + 30s 超时）；并发 30 稳定 33-35 RPS。** 测试：598 passed（2 个 OCR 用例因缺 uploads/contract.png fixture 预存失败，与本次无关）。】**

---

## 六、商业化与市场视角

### 现状问题

1. **无单位经济模型**：`token_usage` 有数据但没按动作核算成本；免费版 5 次/月、团队版"无限"——若 LLM 成本 > 客单价，规模即亏损。V3.0 清单已提出"移除无限调用语义"，需落地为固定上限。
2. **8% 免费→专业转化率是假设**：没有任何定价实验或历史数据支撑，5 次/月的免费额度能否养成习惯值得怀疑（可能应放宽触发"激活"再限）。
3. **B2B 销售动作缺失**：里程碑有里程碑，但没有 demo 脚本、试点合同、客户成功交接流程。10 家律所试点需要"招募→上船→出成果"的运营机制。
4. **渠道杠杆没排优先级**：飞书插件（2026-10 上市场审核）是面向国内中小 B 的最短渠道，应在试点期间就并行做用户教育，而非等到 10 月。
5. **卖点错位风险**：企业客户最该被卖的**不是 AI**，而是"合规（等保+DPA+审计）+ 工作流锁定（门户/计费/签署）"。PRD 自己也承认竞品护城河在工作流。

### 优化动作（商业）

- **M-1 核算单次动作成本**：咨询/审查/文书各 ×模型×token，与定价对账；把团队/企业"无限"改成合同化固定上限，保证毛利为正。**【已完成 2026-08-02：真实库核算（咨询≈0.012元/次、审查全流程≈0.10-0.15元/次、total 2.65元/1866次）+ 毛利对账（免费版≈0.34元/人/月获客成本、团队版 999元 上限 5000/2000/2000 最坏成本≈450元 毛利为正）；团队版无限→固定上限已落地（PLAN_QUOTAS + migration 0052）】**
- **M-2 试点配销售/成功机制**：10 家律所 = 招募话术 + 30 秒 demo 脚本（PRD 已提）+ 试点目标（每人 7 天做满某工作流）+ 退出问卷。
- **M-3 定价实验**：试点后对免费→专业转化做 A/B（额度档位、激活门槛、升级时机），用数据重估 8% 假设。
- **M-4 企业单卖合规+锁定**：销售材料主线 = 等保 + DPA + 审计 + 门户/计费/签署工作流，AI 质量做支撑不是主打。
- **M-5 飞书插件提前并行**：试点期就收集律师使用场景喂给插件设计，10 月上架时带真实案例。
- **M-6 现金流优先级**：先签 2-3 家付费企业 POC（9 月里程碑已有）而非等大范围免费增长——企业 ACV ¥30k 比个人 ¥199/月更快验证商业模式。

---

## 七、优先级排序的优化路线图（按时间，可执行）

> 排序逻辑：先让试点能"测得准"→ 再收范围保主线 → 再补信任/安全 → 同步做商业验证。

### 8 月试点前（现在 → 试点启动）
> 状态核对日期：2026-08-02
| 优先级 | 动作 | 归属 | 状态 |
|---|---|---|---|
| P0 | E-2：关 P0 安全/生产缺口（加密密钥、DR 演练、关半成品开关） | 工程 | 部分完成（密钥/演练/关开关已做；待密钥轮换、RTO/RPO 正式实测） || P0 | E-1：前端改生产构建 | 工程 | 已完成 |
| P0 | P-1：装漏斗埋点 + 周报口径 | 产品 | 已完成 |
| P1 | P-2/P-3：收叙事、隐藏办公自动化、清孤儿页 | 产品/前端 | 已完成 |
| P1 | U-1：角色化引导设默认落地页 | 前端 | 已完成 |
| P1 | E-3：OpenAPI 契约门禁 | 工程 | 已完成 |
| P1 | M-1：单次动作成本核算，落地配额上限 | 数据/商业 | 已完成 |
| P1 | E-7：主路径小压测 | 工程 | 已完成 |

### 试点期间（8–10 月）
| 优先级 | 动作 | 归属 | 状态 |
|---|---|---|---|
| P0 | AI-2：审核反馈回流评测闭环（律师决策 → 新用例） | AI | **已完成 2026-08-04** |
| P0 | U-2/U-4：AI 输出信任三件套 + 端侧反馈 | 前端 | **已完成 2026-08-04：置信度标签（后端动态计算）+ 可点击核对的引用（弹窗含法源/条款/版本/效力状态/生效日期/条文明细）+ 明确下一步（提交审核/导出/生成文书）三线齐备——本次补齐合同审查与文书草稿结果卡的「参考依据」区（此前只有咨询有），与咨询一致支持 openSourceDetail 点击核对；👍/👎 + 原因反馈（AiOutputFeedback）三线已接入后端 feedback_score 端点；前端 vite build 通过** |
| P1 | AI-1：真实语料冻结评测集（100+ 题）扩到 CI | AI |
| P1 | AI-3：法律 Prompt 迁入 prompt_service 灰度 | AI | **已完成 2026-08-04** |
| P1 | E-5：Sentry + OpenTelemetry + 告警 | 工程 | **已完成（2026-08-04 核对）：后端 app/core/telemetry.py（SENTRY_DSN / OTEL_ENABLED / OTLP endpoint 双开关，惰性 import 不阻断启动）+ 前端 main.js 已接 @sentry/vue（VITE_SENTRY_DSN）+ app.config.errorHandler + unhandledrejection 上报；告警走 ALERT_WEBHOOK_URL 三级 webhook** |
| P1 | M-2/M-3：试点成功机制 + 转化 A/B 准备 | 商业/产品 | **M-2 已完成 2026-08-04（pilot-success-playbook.md 补齐）；M-3 准备就绪（配额 DB 化 PLAN_QUOTAS + migration 0052 固定上限，试点后改 DB 计划即可 A/B 额度档位/激活门槛）** |
| P2 | U-3：咨询→审查→文书→审核一键流转 | 前端 | **已完成 2026-08-03** |
| P2 | AI-4：法条引用核验 + 版本效力展示 | AI/前端 | 后端核验部分已完成（verify_source/enrich_references）；前端三线引用可点击核对已随 U-2 落地（2026-08-04） |
| P2 | U-6：移动端至少保障个人咨询流程 | 前端 | **已完成 2026-08-04：App.vue mobile-nav（<760px，律师角色可见工作台/知识库/对话）已存在；本次补 LegalWorkspace <760px 响应式（case-bar 纵向堆叠、case-select 全宽、result-header 按钮紧凑、正文/草稿字号缩小）；el-tabs 自带横向滚动；引导页自适应；vite build 通过** |
| P2 | P-5：北极星 + 7日/30日留存看板 | 产品/工程 | **已完成（2026-08-04 核对）：/api/admin/retention 按注册周分群计算 7/30 日留存（未完全观察窗口显示「—」）+ System.vue「留存与北极星」tab（活跃律师、案件闭环信号、D7/D30 留存表）** |

### 商业化阶段（10–12 月）
| 优先级 | 动作 | 归属 |
|---|---|---|
| P0 | M-4：企业单卖"合规+工作流锁定" | 商业 |
| P0 | M-5：飞书插件带真实案例上架 | 商业/产品 |
| P1 | E-4：拆巨型组件/服务 | 工程 | **部分完成 2026-08-04：LegalWorkspace.vue 从 1718 → 1480 行（-238）——「客户门户」拆为 components/legal/LegalPortalTab.vue（props 传 org/case，内部自持 useLegalCaseCollaboration 状态与加载）、「律师审核」拆为 LegalReviewTab.vue（自持 useLegalReviewQueue + defineExpose(refresh)，父组件 onReviewSubmitted 回调经 ref 触发刷新）；拆出的样式随组件迁移，vite build 通过。剩余：Documents.vue（2365 行）、agent_service.py（116KB）、document_service.py（76KB）待按需继续** |
| P1 | AI-6：更强模型/提示词在评测集上对比决策 | AI | **已完成 2026-08-04：工具 eval/compare_models.py（子进程隔离逐模型跑冻结评测集，--no-llm 基线 100%）；真实对比——qwen-plus 72.3%（合同 9+文书 23+咨询 1）vs qwen-max 94.1%（仅合同 7）vs qwen-turbo 79.8%（合同 24，合同审查场景不可接受）。结论：qwen-max 最优但额度耗尽（恢复后切主模型，切换前先调合同审查 prompt）；当前主模型保持 qwen-plus（qwen-turbo 在核心合同场景退化，不采用）；M-1 单价/毛利按 qwen-plus 口径有效无需重算** |
| P1 | M-6：签 2-3 家付费企业 POC | 商业 |
| P2 | E-6：CI 全量门禁（测试+评测+契约+lint） | 工程 | **已完成 2026-08-04：CI 已有 backend-tests/lint(ruff)/contract-check/eval-regression 四 job；本次为 eval-regression 增加 AI-2 回流回归层门槛（回流用例存在时通过率须 ≥0.95）** |

---

## 八、验证方式（如何用试点数据证明/证伪判断）

本分析不产生代码改动，验证对象是"优化动作是否奏效"，落在试点数据上：

1. **漏斗埋点上线后**，试点周报应能看到：注册→首次任务→7 日留存的每一跳转化率，定位最大流失点。**【2026-08-02 核对：漏斗已上线（/api/admin/funnel + System.vue「用户漏斗」tab），试点周报可直接读取每跳转化率；7 日/30 日留存看板仍待办（属 P-5）。同日修复真实库 schema 漂移：模型有 case_id 而迁移漏写，导致 /api/admin/dashboard 在真实库 500——新增迁移 0053 为 legal_consultations/legal_contract_reviews/legal_drafts 补 case_id 列+索引+FK，已应用至真实库；同时把 /users-stats 的 func.strftime（SQLite 专用）改为跨库 func.date。真实库四端点（dashboard/users-stats/subscription-revenue/funnel）均已实测 200】**
2. **AI-2 回流闭环**跑满 2 周后，重新跑冻结评测集，对比律师通过率、引用正确率、退回原因分布是否改善；记录改善幅度作为"AI 质量自我提升"证据。
3. **E-2/E-3/E-1** 以可交付证据验收：DR 演练记录（RTO/RPO 实测）、生产静态前端、契约门禁在 CI 中"一改即红"。**【2026-08-02 核对：E-1 生产静态前端 ✅、E-3 契约快照+diff 脚本 ✅、E-2 的 DR 演练 ✅；RTO/RPO 正式实测与"CI 一改即红"（E-6 建 CI）仍待办】**
4. **M-1 成本核算**上线后，得出"每动作成本"并与定价对账，确认免费/团队配额毛利为正。**【2026-08-02 核对：核算+对账已完成（咨询≈0.012元/次、审查全流程≈0.10-0.15元/次；免费版≈0.34元/人/月获客成本，团队版 999元 上限 5000/2000/2000 最坏≈450元 毛利为正）；团队版无限→固定上限已落地】**
5. **试点退出问卷 + NPS**（目标 ≥40）验证 U-1/U-2 的用户信任假设是否落地。
6. **AI-1 冻结分层评测集**作为 CI 回归门禁，模型/Prompt/解析器变更必须出报告。**【2026-08-03 核对：generation_eval_dataset.json 扩到 107 题（合同审查 35 + 文书草稿 38 + 法律咨询 34），覆盖三线业务 + 拒答 8 题 + 法条失效 4 题；run_generation_eval.py 新增 --no-llm（强制确定性路径，CI 可复现）与拒答/法条失效分层统计；服务端新增拒答逻辑（_should_refuse：实施意图词 + 违法对象词组合，consultation_payload 在 LLM 调用前拒答），并补齐确定性 fallback 的 NO_VALID_SOURCE 提示与 DISCLAIMER；新增 .github/workflows/ci.yml（pytest + eval-regression，通过率 ≥0.95 且 ≥100 题门槛）。本地验证 107/107 全过（拒答 8/8、法条失效 4/4），pytest 575 全过。注意：仓库当前无 git remote，CI 推送 GitHub 后生效】**
