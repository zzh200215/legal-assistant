# 律智检 · 项目诊断与升级方案（2026-10-12）

> 本文档是一次结构化评审的结论，供后续逐项打勾推进。定位：求职作品集（AI/LLM 应用岗），长期维护。
> 所有事实来自仓库静态核查与真实接口实测，不编造指标。

## 0. 结论先行

- **诊断**：问题不是"功能单薄"，而是三件事叠加——① 前端没把后端能力显性化（入口割裂 / AI 不够显眼 / 视觉不够精致）；② AI 质量从未用真实模型端到端测过；③ 后端过度堆广，焦点被摊薄。能力其实**过剩**，不是不足。
- **升级**：收敛到一条 **Agentic-RAG 主线**，做一个"**懂上下文的右侧法律 AI 助手（copilot）**"把差异化显性化；用真实模型 + 三层评审把质量测出来并沉淀真实数字。

## 1. 现状诊断

| 维度 | 现状（事实） | 判断 |
| --- | --- | --- |
| 规模 | 后端 345 py / ~7.1 万行 / 188 测试；170 commits（2026-08-04→10-11）；前端 71 vue / ~1.68 万行 / 17 视图 | 广度远超一个求职 demo 所需 |
| 技术栈 | Qdrant+Chroma、text-embedding-v3、bge-reranker-v2-m3、BM25+dense+RRF+rerank 混合检索、LangGraph、MCP×3、OTel+Sentry、docker-compose | 栈是真东西，底子好 |
| 核心场景 | 法律咨询 / 合同审查 / 文书草稿 / 客户门户 / 计费 / 飞书 / A2A / 知识图谱… | 场景过多，无单一主线 |
| 功能完整度 | 大多"能跑"，但 AI 质量未被真实测量 | 完整度≠被验证 |
| AI 核心 | Agentic-RAG、多跳、判分、忠实度开关均在，但默认关闭且结论来自**离线桩嵌入**；`eval/outputs/` 为空、`.env` 曾为占位 key | 从没用真实 embedding+LLM 端到端跑过 |
| 交互体验 | Agent 页分解为命令中心/执行视图/侧栏，但入口**仅管理员可见**；Chat 有可点击引用；三个 AI 入口（Chat/工作台咨询/Agent）割裂 | 后端能力在前端被埋 |
| 数据闭环 | 有反馈入库、埋点漏斗面板；但无真实用户、评测样本小（检索 17 题、多跳 27 题） | 闭环成形但缺真实数据 |

## 2. 问题归因（"功能感单薄"的真正来源，按权重排序）

1. **前端没把后端能力显性化（最主要）**：入口割裂/藏得深、AI 不够显眼、视觉不够精致——这三点是用户亲自确认的痛点。
2. **质量从未被真实测量**：评测管线从没用真实 embedding + 真实 LLM 端到端跑过，所以"好不好"无从谈起、更没调过。
3. **过度堆广摊薄焦点**：billing / 飞书 / A2A / 知识图谱 / time-travel 等对求职叙事边际收益低，却分走了精力和注意力。

结论：**不是**"需求定义不清"或"能力堆叠不足"——恰恰相反，能力过剩、呈现不足、验证缺失。

## 3. 升级方向（带优先级）

| 方向 | 目标 | 适用场景 | 预期价值 | 复杂度 |
| --- | --- | --- | --- | --- |
| **A 懂上下文的法律 AI 助手（主线）** | 右侧常驻、可收缩/悬挂、知道当前模块/案件/文书，把 Agentic-RAG 过程显性化 | 咨询/审查/草稿各模块内随手唤出 | 一句话讲清差异化；同时打中三个痛点 | 中 |
| **B 真实模型评测闭环** | 真实 embedding+LLM 跑出质量数字，三层评审 | 面试讲方法论、支撑 A 的"可核验" | 把"质量一般"变成可量化、可讲 | 中 |
| **C 入口收敛** | Chat 并入助手、agent 入口对非管理员可见；保留"展开为整页"深度视图 | 全局导航 | 消除割裂，助手随处可达 | 低 |
| **D 视觉精致化** | 在 Element Plus 上提 2-3 套风格，选定应用到主线 | 主线页面 + 咨询模块 | 第一印象，demo 质感 | 中 |
| **E 商业化/平台化模块** | 保留不删、不投新功能 | 计费/飞书/A2A 等 | 维持完整度，不摊薄主线 | —（冻结） |

> 张力说明：模块全部平权保留（不删代码），但**新投入集中在主线 A/B**。保留 ≠ 继续给每个模块加功能。定位未来若升级，E 可随时解冻。

## 4. 功能清单（面板四层 ↔ 后端三打深点，一个故事）

copilot 面板显示到"全量"四层，每一层由一个后端能力支撑：

| 面板层 | 用户看到 | 后端支撑（打深点） |
| --- | --- | --- |
| ① 工作过程时间线 | 正在检索法源 → 拆解子问题 → 核对引用 → 生成 | 多跳分解（打深点 1）+ WS 流式 |
| ② 证据原文卡 | 命中的法条切片原文摆出来 | 混合检索 + rerank（打深点 3） |
| ③ 逐句溯源 | 答案每句标注来自哪一条 `[①][②]`，可展开核验 | 引用对齐（现有 EvidenceReference） |
| ④ 忠实度标记 | 每条结论旁 ✓已核验 / ⚠️待核验 | 忠实度校验（打深点 2） |

配套：答案区 + 操作（展开为整页 / 复制 / 👍👎 反馈）；"展开为整页"复用现有 `AgentExecutionView` + Chat 引用渲染。

## 5. 技术实现路径

**模型（已配置并实测可用）**
- 聊天/推理：商汤 `glm-5.2`（`https://token.sensenova.cn/v1`，免费）
- 向量：dashscope `text-embedding-v3`（旧 key，1024 维）
- 评审裁判：**`deepseek-v4.1-flash`**（和答题模型不同，规避自评偏置；免费）
- 已加 embedding 独立供应商配置（`EMBEDDING_PROVIDER/EMBEDDING_API_BASE_URL/EMBEDDING_API_KEY`），聊天与向量可来自不同平台。详见 [[llm-provider-sensenova]]。

**方向 A copilot**：在 `App.vue` 认证外壳（router-view 外层）挂常驻右侧面板，收缩成边缘条；上下文注入（当前路由/案件/文书 → 检索范围）；WS 流式推送①过程时间线；复用 `AgentExecutionView`、Chat 引用渲染、`EvidenceReference`；"展开为整页"进深度视图。先只接**法律咨询**模块。

**方向 B 三个打深点**
1. 多跳门槛 `_looks_multi_hop` 修（逗号连接的两问也放行）+ 给子问题结果**单独分配上下文预算**（修 `top_k=8` 时 fan-out 把必需证据挤出 `RAG_CONTEXT_MAX_CHUNKS` 导致 -16.7pt 的问题）。
2. 引用忠实度校验（`AGENTIC_RAG_FAITHFULNESS_CHECK`）接 `glm-5.2` 真模型并测量（此前离线桩测不了）。
3. 混合检索调优：真 embedding 下测 rerank 开关 / 混合权重的实际增益，定默认值。

**评测闭环**：三层裁判 = 规则指标（关键词/引用命中）→ `deepseek-v4.1-flash` LLM-judge → 懂法朋友只看分歧项。跑 `eval/agentic_rag_legal_cases.json`（27 题多跳）+ `legal_retrieval_qa.json`（17 题）+ `document_rag_qa.json`，提交**真实** `eval/outputs/`（此前为空）。

**成本**：聊天/裁判模型均免费；向量按量极低；主要成本是时间。

## 6. 优先级四象限 + 7/30/90 路线图

**四象限**
- 高影响·低投入（先做）：入口收敛(C)、多跳门槛修复、指标表、copilot 外壳 PoC
- 高影响·中投入（主攻）：copilot 四层体验(A)、真实评测闭环(B)、忠实度校验
- 低影响·低投入（顺手）：视觉精致化(D) 的小项、文案去技术化
- 低影响·高投入（不做/冻结）：商业化模块(E) 新功能、LangGraph subgraph 等审美项

### 7 天（本周，真实模型已通，均可落地）
- [x] 多跳门槛修复 + 子问题独立上下文预算。**验收达成（2026-10-17）**：`multi_hop` 臂在 `top_k=5` 覆盖率 1.0000（单跳 0.9815）、`top_k=8` 两侧都是 1.0000（修复前劣 16.7pt）；门槛放行率 3.7% → **100%**，96 题混合语料放行 10.4% 且仅 1 题误判。`AGENTIC_RAG_MULTI_HOP_ENABLED` 默认值随之翻为 True。口径见 `eval/results.md`「真实嵌入复测」，产物 `eval/outputs/agentic_rag_statutes_real_k5_k8.json`。
- [x] 真实管线端到端：用 glm-5.2 + text-embedding-v3 索引 `scripts/legal_corpus`（6 法规 84 片段），跑 27 题多跳 + 17 题检索。**验收达成（2026-10-17）**：27 题多跳见上；17 题检索 Hit@5 = 1.0 / MRR = 0.8562 / 拒答准确率 = 1.0（产物 `eval/outputs/legal_retrieval_report.json`）。口径已记入 `eval/results.md`。遗留：生成与判分节点仍是脚本桩（产物 `mode=real_embedding_with_stubbed_judges`），glm-5.2 的真实生成/判分未接——涉及成本，单独立项。
- [x] 定指标表与目标值（见 §7）。**验收达成（2026-10-17）**：§7「现值」列已按真实实测回填（法规 27 题消融 + 样例 QA 32 题真实生成）。**执行中发现更严重的问题**：`eval/qa_dataset.json` 有 9/32 题的标签与 `eval/fixtures/` 正文对不上——9002 整篇 fixture 已被改写成"律智检平台实施方案"（标题、五个模块、里程碑、风险清单全变），而题面/参考答/关键词仍是旧样例"AI办公助手"；`eval/corpus_manifest.json` 里 9002 的 `document_name` 至今写着"AI办公助手实施方案"，是漂移的铁证。依赖这些标签的指标口径失效，需重写标签后重跑，见 §7 表下注与 `eval/results.md`。
- [x] copilot 外壳 PoC：右侧可收缩面板，在"法律咨询"接通一次问答，显示答案 + 可点击引用（先不做全四层）。**已交付**（`frontend/src/components/CopilotDock.vue` + `components/copilot/` + `composables/useCopilotContext.js`，构建/lint/单测/6 个 e2e 全绿）。**遗留口径**：面板与真后端从未在同一次会话里联通过（两侧各自为真，装配是推理而非实测）→ 见 `docs/review/step1-copilot-poc-handoff.md`。

### 30 天
- [ ] copilot 四层体验在"法律咨询"跑全：过程时间线（WS 流式）+ 证据卡 + 逐句溯源 + 忠实度标记。**验收**：咨询一问能看到完整四层。**进度（2026-10-17）**：四层已在面板落地（`frontend/src/components/copilot/` + `composables/copilotLayers.js`，单元 58 passed / copilot e2e 9 passed / lint 与 build 全绿），但有**两处如实降级**，故未打勾——① 时间线是「事后回放」：`/api/ws/agent` 只跑通用 planner（`app/api/conversation/ws_api.py:340 → :392`），没有法律咨询意图，REST 序列化（`app/services/legal/legal_workspace_service.py:122-138`）也没有 trace/steps（**已于 2026-10-18 修复**：咨询链路现在落库并返回结构化 `trace`，面板显示服务端真实步骤与耗时，见下方补记），界面上常显"不是服务端的实时事件流"；要真流式得让咨询走 WS 事件流或后端补 trace 字段（**已于 2026-10-19 修复**：咨询改走 `/api/ws/chat` 的 `consult` 消息，管线每步实时推送 `consult_step`，答复未到面板就先逐帧显示步骤，见下方补记）。② 逐句溯源退化成**整篇答案粒度**（后端不给句级引用映射），界面常显"不是该句的专属依据"（**已于 2026-10-18 修复**：咨询链路现在落库并返回句级映射 `attribution`，面板点某句只高亮该句依据，见下方补记）。③ 忠实度常态显示"未校验"（该链路不返回该字段）（**已于 2026-10-19 修复**：新增「生成后补判」接口，答案先返回、随后由判官逐条核对并落库，面板标记从「校验中」翻转为「通过 / 未通过」，见下方补记）。另修掉一个真缺陷：面板原来在**每个页面**都发 `GET /legal/consultations`（`useLegalConsultations` 的查询默认启用），已加 `withHistory` 开关，dock 传 `false`；全套 e2e 的"不得有多余请求"哨兵失败数 26 → 18，剩余 18 例已抽样确认是**早于本次的 spec 漂移**（例：`frontend-engineering.spec.js:68` 仍断言导航文案"系统中心"，而代码在 HEAD 早已改名为"系统"）。
- [x] **补记（2026-10-18）：时间线改为服务端 trace**。咨询链路现在在 `app/services/legal/legal_service.py` 的 `consultation_payload()` 内记录结构化过程（检索法源 / 生成建议 / 抽取事实 / 核验引用，每步带 `status` / `duration_ms` / `detail`），落库到新列 `legal_consultations.trace_json`（迁移 `alembic/versions/20261018_0097_consultation_trace.py`），随 `serialize_workspace_row` 以 `trace` 字段返回；面板有 trace 时标题显示「服务端步骤」并逐步显示状态与耗时，无 trace（老记录 / 追问链路）自动回退「事后回放」。**证据**：真机 `GET /api/legal/consultations/23` 读回 4 步 trace（`生成建议 16840ms · 模型 glm-5.2 · 提示词 v1` 等），DB 侧 `trace_json` 497–520 字符；面板实拍 `docs/review/step7-trace-real-panel.png`；后端 56 passed、前端单元 66 passed、`copilot-dock` 11 passed、全量 e2e 43 passed / 0 failed（含 4 条真后端集成用例）。**边界**：① 咨询是单跳链路，trace 无子问题/多跳轮次（`metrics.rounds` 恒 1，不编造）；② 追问链路未埋点；③ 实测发现 LLM 网关偶发秒级失败（`Event loop is closed`，3 次中 1 次），trace 如实标「降级」（**根因已于 2026-10-18 修复**：`app/core/llm_client.py` 的连接池跨事件循环复用——临时循环关闭后 keep-alive 连接被主循环复用即抛该错，表现为「第二次咨询直接降级」；改为每循环一个 client 并加 3 条回归用例；网关侧超时/限流仍属外部风险，需观察）。**E 项收尾（2026-10-18）**：修复后真实数据 10 次连续生成 0 降级（修复前 24 条中 5 条秒级降级 17–90ms，全部 ≤ id 32），成功耗时 min 6.34s / p50 14.19s / max 31.66s 均在 60s 超时内，未再出现"长时间不返回"；新增 `tests/test_legal_gateway_resilience.py`（6 例）守边界——网关超时/连接错误被 `_llm_chat` 吞掉返回 None、咨询链路仍给可用答复且 trace 如实标 `degraded`、客户端必须带配置超时创建、重试预算有界（最坏是"超时×重试后降级"而非无限挂起）。详见 `docs/review/step12-llm-gateway-resilience.md`。详见 `docs/review/step7-copilot-server-trace.md`。
- [x] **补记（2026-10-18）：逐句溯源落地 + 引用核验做到条级**。咨询写入时计算「答案句 → 引用条目」映射（jieba 词面重叠打分，阈值 0.34，每句最多 2 条依据），落库到新列 `legal_consultations.attribution_json`（迁移 `alembic/versions/20261018_0098_consultation_attribution.py`），随 REST 以 `attribution` 返回；面板有服务端句级映射时点某句**只高亮该句依据**，服务端声明句级但与本地分句对不齐时回退整篇粒度并说明原因。同时把引用核验从「有没有关联到法源」推进到「条号对不对」：`app/services/legal/legal_reference_service.py` 解析阿拉伯/中文数字条号并与 `legal_articles` 实际收录条文核对，答案正文里出现的条号并入核验范围，引用→法源匹配放宽三级（标题归一化相等 → 包含 → 条号交集 + 标题亲和），并给同一次咨询的重复引用去重。**证据**：真机 consultation 36（demo_reviewer / 案件 #4）`attribution` 与本地分句完全对齐、**7/9 句**匹配到依据（点第 2 句只高亮 1 张证据卡），`核验引用 完成 2ms 0 条已核验 · 1 条未核验`，basis 逐字为「引用的第83条未在已收录的 17 条条文中找到（可能是该条未收录，也可能引用有误）」；截图 `docs/review/step9-attribution-panel.png` / `step9-attribution-sentence.png`；后端 50 passed（溯源+核验，子集 75 passed）、前端单元 71 passed、lint 0 error / 13 warning、build exit 0、`copilot-dock` 13 passed。**边界**：① 句级映射是词面重叠不是语义匹配（总结句/转述句可能匹配不上，本次 2/9 句留空）；② demo 语料《劳动合同法》只收录 17 条，模型引用的第 83 条（真法存在）会显示未核验——文案已区分「未收录」与「引用有误」；③ 追问链路未埋点；④ 老记录不回填。详见 `docs/review/step9-sentence-attribution.md`。
- [x] **补记（2026-10-19）：第四层「生成后忠实度校验」在线落地**。新增 `POST /api/legal/consultations/{id}/faithfulness`（`refresh` 可重判），判官复用 `app/services/rag/agentic_judge.check_faithfulness`，结论落库到新列 `legal_consultations.faithfulness_json`（迁移 `alembic/versions/20261019_0099_consultation_faithfulness.py`）并随详情/列表以 `faithfulness` 返回。**关键决策**：判官**不在生成路径上**（真机一次判官 10.8s，塞进创建请求会让「提问→答案」多等一个数量级）——答案先渲染，前端拿到 `id` 后再补判，标记经历「忠实度校验中… → 通过 / 未通过」，未通过时列出未被支持的断言；`available=false`（判官不可用）**不落库**，留空可重试；已有结论不被响应里的空值覆盖，二次读取 0.0s 命中缓存。**判官证据 = 本次引用条文 + 本次材料正文**：真机实测发现只给法源条文时，带合同/文书提问的答复会被误判未通过（付款金额、仲裁请求本就在用户材料里）——补上材料证据后同一份答复从 `faithful=false`（3 条 unsupported、`evidence_count=1`）翻成 `faithful=true`（`evidence_count=2`）；材料读权限与提问共用一套口径（抽出 `_material_readable()`：本人 / 管理员·审核人 / 案件成员），材料 id 从 `trace_json.contexts` 读回，不新增列。**证据**：真机 consultation 46/47 首次 10.8s 出结论并落库、二次 0.0s 回读、越权 404；面板实拍 `docs/review/step14-faith-checking.png`（校验中）/ `step14-faith-pass.png`（通过，理由「付款金额、期限及仲裁机构均与片段3一致，无缺依据断言。」）；后端 `tests/test_legal_faithfulness.py` 18 例 + 上下文 14 例 → **32 passed**，前端单元 74 passed，`copilot-dock` + `copilot-case-context` → **17 passed**。**边界**：① 判官与生成同源（glm-5.2），跨模型一致性只在离线评测做过（见下条）；② 一次判官一次成本，`refresh=true` 才重判；③ 历史列表已透出字段但 UI 未呈现。详见 `docs/review/step14-copilot-faithfulness.md`。
- [x] **补记（2026-10-19）：时间线改成真流式（B1）**。咨询提问不再走「REST 一次性返回」，而是走已有 WS 通道 `/api/ws/chat` 的新消息类型 `consult`：`app/services/legal/legal_service.py` 的 `consultation_payload()` 新增 `step_callback`，每完成一步（检索法源 / 生成建议 / 抽取事实 / 核验引用）就推送一条 `consult_step`（带 `status` / `duration_ms` / `detail`），最后一条 `consult_done` 携带完整结果（含 `trace` / `attribution` / `faithfulness` 等字段，形状与 REST 一致）。**关键决策**：① 复用既有 WS 通道与鉴权（`?token=` / Authorization / Sec-WebSocket-Protocol），不新开端点；② 结果仍由服务端落库并整包下发（不逐字流式），`trace` 的语义从「事后记录」升级为「同一批步骤，边跑边推、最后落库」；③ 前端**优先走 WS，连不上才回退 REST**（回退时行为与之前逐字相同）；④ 面板在答复到达前显示「实时步骤」块与「以上步骤由服务端在处理过程中实时推送」说明，答复到达后让位给正式 trace。**证据**：真机实拍 `docs/review/step15-stream-live.png`（生成中：`检索法源 完成 467ms · 带回 4 条候选法源`，此时**答复尚未出现**）与 `step15-stream-done.png`（答复 + 4 步 trace，`生成建议 完成 18.4s`）；后端新增 `tests/test_ws_consult_stream.py`（8 例：逐帧顺序、空内容拒绝、配额/上下文冲突/不存在映射、旧 `chat` 类型兼容）全绿，前端新增 `frontend/src/utils/consultSocket.js` + 6 例单测、`copilot-dock` 16 passed（含「WS 逐帧」与「WS 不可用回退 REST」两条新用例）；契约文档 `docs/websocket-protocol.md` 与 `docs/ws-events.schema.json` 同步新增 `consult` / `consult_step` / `consult_done`。**边界**：① 逐字流式（token 级）未做，仍是「步骤流式 + 结果整包」；② 追问链路不推步骤（与 trace 同口径）；③ WS 断开或 4s 内未连上即回退 REST，回退后无实时步骤（但答复与 trace 完整）。详见 `docs/review/step15-live-streaming.md`。
- [x] 忠实度校验接 glm-5.2 真模型并测量。**验收达成（2026-10-17）**：31 个真实答案逐题判分 **31/31 faithful、0 条不支持陈述**（`scripts/run_faithfulness_eval.py` → `eval/outputs/faithfulness_report.json`，口径与限制见 §7 注）。**「能正确标出未入库法条」已核验**：`app/services/legal/legal_reference_service.py:23` 的 `_UNVERIFIED_NOTE = "未关联法源库条目，无法核验"` 会给未命中 `legal_sources` 的引用打 `verification.verified = false`，`tests/test_legal_reference_verification.py` 13 passed（含 `:136` 对该 note 的断言），首轮真实咨询响应（形状 A）已实测带此标记。**遗留**：裁判与被测同为 glm-5.2，须按 §8 接 cross-model（deepseek-v4.1-flash）复测同一批答案。
- [ ] 三层评审落地：规则 → deepseek-v4.1-flash LLM-judge → 人工抽检分歧项；评审脚本 + 一轮真实结果。**验收**：AI judge 与人工抽检一致率 ≥ 0.8。**进度（2026-10-19）**：① 规则层与 ② 跨模型裁判层已完成（数字表见 `docs/review/step13-three-layer-review.md`：规则层 35 题 hit@k/mrr/ndcg@5 = 1.0、引用准确率 1.0、拒答准确率 1.0、badcase 0；跨模型裁判 glm-5.2 vs qwen-plus 忠实度均 1.0（31/31）、三维差 ≥0.1 仅 1 题、verdict 变化 0）；③ 人工层**工具与抽样表已就绪、等 25 条人工结论**（`eval/outputs/human_spotcheck_sheet.md`，分批判定中；评分 `scripts/score_human_review.py --threshold 0.8` 会直接打印达成与否，抽样表 25 条经校验两位裁判判罚均可解析、无静默缩分母）。**口径偏差如实记录**：诊断原口径的裁判是 `deepseek-v4.1-flash`，本机 `.env` 无任何 deepseek 凭据，经用户裁决（2026-10-18）沿用 **qwen-plus**（dashscope 兼容）作为跨模型裁判，若后续提供 deepseek 凭据可用 `scripts/run_cross_judge.py --judge-model deepseek-v4.1-flash` 原样重跑。**当前未达成**（人工层未填）。
- [x] 入口收敛：Chat 并入 copilot、保留"展开整页"深度视图；agent 入口对非管理员可见。**验收达成（2026-10-18）**：① 三入口归一——工作台首页「案件助手」快捷入口、案件工作台「打开助手」、导航开关全部改为**打开右侧面板**，不再离开当前页面跳 `/chat`；面板头部新增「整页」按钮作为保留的深度视图（带 `case_id` 进 `/chat`，`CopilotAnswer` 的逐答案「展开为整页」仍指向案件咨询 tab）。实现：新增单例 composable `frontend/src/composables/useCopilotDock.js`（`openCopilot/closeCopilot/toggleCopilot`，模块作用域共享 ref），`frontend/src/App.vue` 的面板开合改由它托管，`frontend/src/views/LegalWorkspace.vue` 两处入口（概览侧栏「打开助手」、`@open-chat`）改调 `openCopilot()`。② agent 入口对非管理员可见——后端 `/agent/*` 本就只有 `get_current_user`（`app/api/agent/agent_api.py`，运行/日志/检查点/审批全按 `user_id` 隔离；只有 `app/api/agent/mcp_api.py:72` 的 `sql_query_tool` 在后端按 admin 拦截），因此只需改前端：`frontend/src/auth/capabilities.js` 把 `AGENT_VIEW/AGENT_RUN/AGENT_CANCEL` 移入 `USER_CAPS`，`App.vue` 的 Agent 导航项从「管理中心」组移入「工作区」组并去掉 `adminOnly`（系统/管理中心/评测与审计/计费仍 adminOnly）。**证据**：真后端以 `demo_lawyer`（role=user，org 15）实测 `/agent/registry`、`/agent/runs`、`/agent/metrics`、`/agent/approvals` **全 200**；普通用户视角实拍 `docs/review/step8-converge-workbench.png`（导航含 Agent、工作台含「案件助手」入口、面板头部含「整页」）、`step8-converge-dock.png`、`step8-converge-agent-user.png`（进入 Agent 页无 403）；新增回归 `frontend/tests/e2e/entry-convergence.spec.js`（4 例：两个入口开面板不跳页 / 整页带 case_id / 普通用户 Agent 入口可见可进），并同步更新旧契约 `frontend/tests/e2e/frontend-engineering.spec.js:62`（原断言「非管理员看不到 Agent」→ 现在断言可见）。详见 `docs/review/step8-entry-convergence.md`。
- [x] 视觉：提 2-3 套 EP 风格，选定并应用到 copilot + 咨询模块。**验收达成（2026-10-18）**：选定 `docs/review/ui-preview-direction-b.html`（靛蓝 #4F46E5 + 青绿 #0D9488）为基准并落地——新增外壳顶栏面包屑（`App.vue` + `composables/workspaceBreadcrumb.js`）、工作台首页、案件工作台（页头 / 概览卡片 / 案件卡片网格 / 咨询结果卡）、右侧 copilot 面板；共享版式类集中在 `styles/design-tokens.css`（`.app-card` / `.app-stat` / `.app-tag` / `.app-thread` / `.app-question-bubble` / `.app-evidence*` 等），组件不再各写一份。**证据**：lint 0 error / 45 warning、单元 58 passed、`npm run build` exit 0、全套 e2e **37 passed / 3 skipped / 0 failed**（`verify-u2-u4` 需真实后端，按 `E2E_RUN_INTEGRATION` 跳过）；截图见 `docs/review/step2-*.png`。**第二轮（同日）**把侧栏四个一级落点也收敛到同一套共享类：文档页、任务页、法律资料（研究）页、通知中心（各自 e2e：`frontend-engineering` 4 / `contract-upload` 1 / `tasks-flow` 3 / `system-feedback-tasks` 1 / `sources-flow` 1 passed），并修掉 `DocumentWorkspace.vue` 里两个真实版式缺陷（卡片头标题与摘要瓦片在窄栏被挤成竖排）。**第三轮（同日）**收管理与商业化模块：审核 tab（顺带修三个真缺陷：分配下拉被挤成竖排、`catch (error)` 引用不存在的 ref、队列项缺 flex 布局）、系统页（页头 + 概览条 + 浅色 tab 栏 + 健康检查/反馈/任务/组织四个 tab）、管理中心、Agent 自动化执行（四个子组件）、计时计费、开发者与安全管理；复跑后 lint **0 error / 35 warning**、单元 58 passed、build exit 0、全套 e2e **39 passed / 3 skipped / 0 failed**，截图 `docs/review/step3-*.png`。**第四轮（同日）**收客户门户与案件工作台剩余 tab：公开客户门户页（`LegalPortal.vue`，品牌页头 + 进展时间线 + 公开文档 + 账单数据卡）、案件门户 tab、关键日期、文书、合同审查、合同台账；案件 tab 页头统一（合同审查与审核两个 tab 因组件自带页头而外层不加，避免双标题）；顺带把全站硬编码颜色清零（11 个文件 33 处换令牌，新增文稿高亮令牌 `--color-highlight-*`，删除 Tailwind 遗留调色板与 `legal-workspace.css` 死类）。复跑后 lint **0 error / 32 warning**、单元 58 passed、build exit 0（入口 145.92 kB raw / 51.03 gzip）、全套 e2e **39 passed / 3 skipped / 0 failed**（含 2 条临时截图用例，删后 37/3/0），截图 `docs/review/step4-*.png`（7 张，全部无失败请求、无运行时报错）。**第五轮（同日）**收系统页 11 个数据密集 tab（Token 统计 / 操作日志 / 告警 / 实验观测 / 事件漏斗 / 留存 / 工具健康 / 审批台 / 知识库 / 敏感治理）：先扫全仓确认**没有任何图表依赖**（echarts/canvas 命中 0），于是口径定为「筛选条 `.app-filter-bar` + `.app-stat` 指标网格 + `el-table` 包 `.app-table-wrap` + 数值列 `.app-num`」，不为好看引图表库；10 个组件里 `el-card`/`el-statistic`/`el-descriptions`/`el-row`/`el-col` 归零。**同时把「真后端集成」从待办变成事实**：起 MySQL+Redis+后端（127.0.0.1:8001），新增 `verify-system-real.spec.js`（15 个 tab 逐个走查：0 个 `#runtime-error-panel`、0 pageerror、0 个 5xx），并把 `verify-u2-u4.spec.js` 从「对着写死的 mock 断言」升级为「对真实法源库断言」（`resolveConsultReference` 解析真实法源，断言标题/版本/施行日期/条文首段；法源库缺条文时用例直接失败并提示先跑 `scripts/import_legal_sources.py`）——**3 passed**。集成跑暴露并修掉两个真缺陷：① 模型产出的 `references` 里只要有一个 `null` 条目，`v-for` 取 `r.source_id` 就抛错、整块结果卡白屏 → 新增 `utils/references.js` 的 `asReferenceList()` 统一过滤（+3 条单测），落到咨询/文书/审核/文档分析四处；② `useLegalSourceDetail` 的共享状态**此前没有任何组件渲染**（点引用无反应，step1 已记录）→ 新增 `SourceDetailDialog.vue` 挂到案件工作台，并给单例加 `surface` 参数（`legal`/`copilot`）避免 copilot 内联展开与弹窗同时触发。另外定位并修掉一类 e2e 偶发失败：冷启动时 Vite 按需转换重型路由模块图超过用例 30s 默认超时（失败用例每次不同、热跑必过，agent-flow 首个用例冷 27.4s vs 热 1.3s）→ `vite.config.js` 增加 `server.warmup.clientFiles`、`playwright.config.js` 放宽 `timeout`/`navigationTimeout`，修复后同批 spec 冷启动 8 passed / 19.9s。复跑后 lint **0 error / 13 warning**、单元 **61 passed**、build exit 0、全套 e2e **41 passed / 0 failed**（含 4 条真后端集成用例；不带 `E2E_RUN_INTEGRATION` 时 37 passed / 4 skipped），截图累计 36 张（含系统页 15 个 tab 的真后端整页实拍与法源详情弹窗）。**未覆盖**：页面收敛已无遗留（系统组织 tab 的三张表单卡与数据密集 tab 的宽表格是刻意保留的 e2e 锚点/口径）；`npm run bundle:check` 仍红（入口 145.9/120 raw、51.0/42 gzip、总资产 1642.8/1600，属既有开口项）；copilot 的两处如实降级（过程时间线为事后回放、逐句溯源为整篇粒度）仍需后端补 trace / 句级引用映射。详见 `docs/review/step2-direction-b-visual.md`。
- [x] **补记（2026-10-18）：包体预算四项全绿（方向 D）**。`npm run bundle:check` 从「入口 raw 145.6/120、gzip 50.0/42、总资产 1642.8/1600 三项红」变为**四项达标**：入口 raw **102.0/120**、gzip **35.8/42**、最大页面 chunk 121.8/130（System）、总资产 **1579.0/1600**（入口 raw −30%）。根因与手法：① **32 个文件**用 `import { ElMessage } from 'element-plus'` 桶导入，把整库模块图（含各组件 CSS 副作用）拖进入口 → 全部改为 `element-plus/es/components/<c>/index` + `style/css` 深导入（仓库既有约定）；② `frontend/src/main.js` 里 `ElAside/ElContainer/ElHeader/ElMain/ElMenu/ElMenuItem` 全仓零使用 → 删除注册与样式导入；③ 最后两处 `el-row/el-col`（`LegalSourcesTab.vue` 新建资料对话框、`SystemOrgTab.vue` 组织/部门/用户归属）是 35.6 KB `el-col.css` 的唯一引入者 → 换成原生 CSS 栅格（`.source-form-row` / `.system-block-row.is-split`，含响应式单列）。**决策**：D-1 达标后**不放宽也不下调**红线，只把本次实测写进 `frontend/scripts/check-bundle-budget.mjs` 的预算注释（证明该红线可达）。**证据**：`npm run bundle:check` exit 0；lint 0 error / 10 warning；单元 71 passed；真后端实拍 `docs/review/step10-grid-orgs.png`、`step10-grid-source-dialog.png`（DOM 断言 `ORG_ROWS 3 / ORG_CELLS 6`、`DIALOG_ROWS 4 / DIALOG_CELLS 9`）。详见 `docs/review/step10-bundle-budget.md`。

### 90 天
- [x] copilot 扩到文书审查 + 文书草稿（各自上下文注入）。**验收达成（2026-10-18）**：用户在案件工作台的「合同审查」/「文书」tab 选中某一份材料时，该 tab 把选中项 id 写进 URL（`contract_id` / `draft_id`），右栏助手据此在提问时带上它（接口字段 `contract_review_id` / `draft_id`）——**前端只传 id，正文由后端按 id 读取并按 `user_id` 校验归属**；后端把"本次实际参考了哪份、多少字"写进 `trace.contexts`（与 `steps` / `metrics` 同级）。**证据**：真机 `POST /api/legal/consultations {case_id:4, contract_review_id:1}` → 200 且 `trace.contexts = [{type: contract_review, id: 1, title: 技术服务合同（演示）, chars: 85}]`，答复实际引用了合同付款条款；`{draft_id:2}` → `chars: 317` 并说出草稿里的仲裁请求；越权/不存在 id → **404 `LEGAL_CONTEXT_NOT_FOUND`**；两份同时给 → **400 `LEGAL_CONTEXT_CONFLICT`**；截图 `docs/review/step11-context-dock.png`（面板上下文条「当前：合同审查 · 案件 #4 · 合同 #1」）；后端新单测 14 passed、回归子集 588 passed / 1 failed（既有环境不一致，与本项无关）、前端单元 74 passed、lint 0 error / 10 warning、新增 e2e `copilot-case-context` 3 passed、相关 e2e 回归 22 passed。**边界**：① `chars` 是截断后实际送进提示词的字符数，不是原文全长；② 一次只带一份材料（互斥）；③ 只覆盖合同审查与文书两个 tab，合同台账不发布当前对象；④ 恢复依赖已加载列表（无单条 GET）。详见 `docs/review/step11-copilot-material-context.md`。
- [x] 混合检索调优：真 embedding 下测 rerank 开关/权重并定默认。**验收达成（2026-10-19）**：新增 `scripts/run_hybrid_retrieval_ablation.py`，在**生产检索路径**（`LegalRetrievalService.search`）上对 18 部法规 / 17 题 / top_k=5 / 真实 `text-embedding-v3` 跑六臂消融（词级 only、RRF k=10/60/200、稠密候选 30→10、LLM 重排），产物 `eval/outputs/hybrid_retrieval_ablation.json`，表格与结论写入 `eval/results.md`「混合检索消融」。**结论**：① RRF 常数不敏感（k=10/60/200 的 Hit@5 与 MRR **完全一致** 0.8802）→ 保持默认 `RAG_RRF_K=60`；② 稠密候选宽度同样不敏感（30→10 无变化）→ 保持 30；③ **LLM 重排是唯一有效杠杆**（MRR 0.8802→0.9531）但耗时 0.1–0.8s → **209.4s**（≈270×），**不设为默认**，保留为高价值查询可选档；④ 六臂的拒答准确率与「避让失效法源」违规都是 1.0 / 0。**边界（如实）**：本套评测 Hit@5 六臂全 1.0，语料太小/太易，**不足以支撑更细的调优结论**；要真调优得先做有区分度的检索集（同法条干扰项、跨法条多跳）。顺带发现开口项：BM25 索引路径偶发 `AttributeError` 后回退 jieba 全表扫描（功能正确、只是慢），未修。
- [x] 5 分钟 demo 脚本 + 录制真实轨迹兜底 + 简历 bullet（带真实数字）。**验收达成（2026-10-18）**：脚本 `docs/review/demo-5min.md`（前置条件表 / 8 拍时间轴（每拍画面+话术+停留秒数）/ 备用提问 / 断网兜底 / 复现命令 / 简历 bullet / Q&A 预案）；**兜底是真实轨迹**——录制器 `frontend/tests/e2e/demo-record.spec.js`（`E2E_RUN_INTEGRATION=true` + `E2E_DEMO_*` 才跑，真实后端 + 真实 LLM，不 mock）一条命令跑完 8 拍并留下 8 张截图与 webm 录像，实测 **1 passed (33.6s)**，产物归档在 `docs/review/demo/`（`01-workbench` → `08-full-page` 共 8 张 + `demo-run.webm` 2.1 MB）；**简历数字可溯源**——新增 `scripts/demo_metrics.py` 现读 `eval/outputs/*.json` 与构建产物打印（规则层 35 题 hit@5/证据覆盖率/引用准确率/拒答准确率 = 100%、badcase 0、p50 17.0s/p95 52.3s；裁判层 glm-5.2 × qwen-plus 忠实度 31/31 一致、分歧 0；`--with-bundle` 追加包体四项），文档里每个数字都标注了复现命令。**边界**：① 录制依赖真实 LLM 往返（两次咨询各 12–13s），LLM 网关长尾失败时需重录；② demo 账号 `demo_reviewer` 咨询配额 5 次/月，重录前需重置；③ 现场演示必须用真实后端，mock 会丢掉 trace/句级依据/条级核验三个亮点。详见 `docs/review/demo-5min.md`。
- [x] 固化 baseline 快照防回归。**验收达成（2026-10-19）**：新增 `scripts/eval_baseline.py`（`--write` 固化 / `--check` 回归门禁（退出码 2 = 检出回归或配置漂移）/ `--show` 打印），快照落 `eval/outputs/baseline_snapshot.json`（35 题，1991 B）并附 `baseline_badcases.json` / `baseline_summary.json`；`eval/run_experiments.py --check-regression` 与「评测与审计」页的 baseline 区块从此有数可读。**锚定**：`git_commit 78c8514`、`prompt_version 1`、`top_k 5`、`confidence_threshold 0.35`、`llm_model glm-5.2`、`embedding_model text-embedding-v3`、`generated_at 2026-10-19T03:08:26Z`——参数或 prompt 版本漂移会被 `--check` 直接点名。**实测指标**：hit_at_k / mrr / citation_accuracy / refusal_accuracy / 两个证据覆盖率全部 1.0、badcase 0。**边界（如实）**：fixture 语料上指标已饱和，快照的价值是**防回归**（掉分立刻可见），不是区分度；有区分度的数字在 `qa_eval_repaired.json` 与消融报告里。复现：`CHROMA_PERSIST_DIR=./.tmp_chroma_eval`、`EVAL_CASE_PAUSE_SECONDS=12`（免费网关 rpm/tpm 很低，按题节流）、产物先写仓库根再 `Move-Item`（本机 python 子进程不能写工作区子目录）。另记开口项：BM25 索引路径偶发 `AttributeError` 后回退 jieba 全表扫描（`app/services/rag/retrieval.py:245`，功能正确、只是慢），未修。

## 7. 风险与验证

**风险**
- 产品：主线收敛后其他模块显旧 → 对策：冻结不删，demo 只讲主线。
- 技术：免费模型额度/限流波动；多跳 fan-out 挤占上下文（已列入修复）；忠实度判定依赖裁判模型质量。
- 数据：评测样本小（17/27 题），结论口径需如实标注；无真实用户。
- 成本：免费为主，但依赖商汤 Token Plan 公测延续；需留本地 bge-m3 兜底（HF 缓存已有）。

**指标口径与目标值**（「现值」列已于 2026-10-17 回填真实 baseline，口径见下）

| 指标 | 现值 | 目标 |
| --- | --- | --- |
| 证据覆盖率 @top_k=5 | 法规 27 题 **0.9815**（single_hop，真实嵌入）；样例 QA 35 题 **1.0** | ≥ 0.95 |
| 全覆盖率 @top_k=5 | 法规 **0.9630** → 开多跳 **1.0000**；样例 QA 35 题 **1.0** | ≥ 0.90 |
| 引用准确率 citation accuracy | 样例 QA 35 题 **1.0**（badcase 0） | ≥ 0.93（真实） |
| 拒答准确率 | **1.0**（4/4 应拒答题全对；标注修复后无任何误拒） | = 1.0 |
| 忠实度 faithfulness | **1.0**（31/31，`check_faithfulness` + glm-5.2 逐题判分，不支持陈述 0 条） | ≥ 0.90（无幻觉法条） |
| 成本 avg LLM calls/问答 | 检索层 single_hop **0.00** / multi_hop **1.00**；生成+裁判 ≈ **2** | ≤ single_hop + 1 |
| P95 延迟 | **52.35 s**（样例 QA 35 题真实生成；p50 16.98 s、均值 26.24 s） | 建基线后定 |
| AI judge vs 人工 一致率 | **未测**（需人工标注一批，本次未做） | ≥ 0.80 |

**口径注（2026-10-17 实测）**
- 数据源 ①：`eval/run_agentic_rag_eval.py --corpus-kind statutes --top-k 5 8 --real-embedding`，27 题 × 4 臂 × top_k 5/8，真实 `text-embedding-v3` + `scripts/legal_corpus`（6 法规 / 84 片段），**生成与判分仍是脚本桩** → `eval/outputs/agentic_rag_statutes_real_k5_k8.json`。
- 数据源 ②：`scripts/run_qa_eval_real.py` 跑 35 题样例 QA（真实嵌入 + 真实 `glm-5.2` 生成 + 真实 LLM 裁判，节流 10 s/次）→ `eval/outputs/qa_eval_repaired.json`。
- **样例 QA 集的标签修过一次（2026-10-17）**：原 32 题里有 9 题的 `expected_chunk_keywords` 在其目标文档正文里查无实据（9002 那批题面照的是另一个样例"AI办公助手"，而 fixture 正文早已是"律智检平台实施方案"）。按正文重写并补齐覆盖后变成 35 题（31 可答 + 4 应拒答），复测全部关键词命中。修标签前那版口径（hit@5 0.7931 / 引用准确率 0.7586 / badcase 7）**是标注失效造成的低分，不是产品缺陷**，详见 `eval/results.md`。
- 顺带修掉一个真缺陷：证据评估节点的门槛曾写作 `max(配置值, 0.45)`，把低于 0.45 的 `RAG_CONFIDENCE_THRESHOLD` 静默抬高 0.1，与同链路上 `answer_from_chunks_async` 的判定不一致（实测把 confidence 0.442 的正确证据判成拒答）。已改为只取配置值并补两个单测。
- 数据源 ③：`scripts/run_faithfulness_eval.py` 拿 ② 里 31 个「可答且已作答」的答案，与各题 `citations[].source_text` 一起喂 `app/services/rag/agentic_judge.py::check_faithfulness`（真实 glm-5.2）逐题判分 → `eval/outputs/faithfulness_report.json`，31/31 faithful、0 条不支持陈述。
- **忠实度这个 1.0 只算“自家口径下没发现编造”**：裁判与被测都是 `glm-5.2`（不是 §8 定的 deepseek-v4.1-flash 交叉模型），且只喂了写进答案的引用片段、不是完整 top-5 上下文；`AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED` 仍默认 `False`（开着等于每可答题多加一次 LLM 往返）。要当上线依据，须先接交叉模型复测同一批答案。
- 遗留：生成/判分节点在法规消融里仍是脚本桩；裁判与被测模型同族，groundedness 类分数偏高；「AI judge vs 人工一致率」仍需人工标注。

## 8. 已定决策（本次评审结论）

- 定位：求职作品集、长期维护；商业化模块保留不投新功能（未来定位可解冻）。
- 主线：懂上下文的右侧 copilot，承载 Agentic-RAG 法规/案例问答。
- 面板：全量四层（过程时间线 / 证据卡 / 逐句溯源 / 忠实度标记）。
- 打深点：多跳门槛+预算、忠实度校验、混合检索调优。
- 裁判：规则 → deepseek-v4.1-flash（交叉模型）→ 人工抽检分歧项。
- 结构：Chat 并入、保留展开整页深度视图；先接法律咨询一个模块；视觉提 2-3 套 EP 风格选。
- 模型：glm-5.2（聊天）+ text-embedding-v3（向量），已实测可用。

## 9. 技术深化决策（六维度 · 2026-10-12 第二轮）

**基调**：面试展示优先——每维度做一个能讲深的亮点，不追生产级规模。
**架构假设**：单用户工作台为主，权限/数据可见性边界留清晰接口，不提前实现多租户。

| 维度 | 打深的亮点（决策） |
| --- | --- |
| 前端 | 延续现有架构：vue-query 管数据、轻 Pinia 管会话态；copilot 过程时间线复用现有 WS 通道（`wsClient`/`agentSocket`）流式推送，不引入 SSE / 重状态库 |
| 后端 | 守现有 ADR 边界（modular monolith）：copilot 走现有 agent 上下文（`agent_runtime`/LangGraph）+ rag 服务 + 薄 API 层，不单开模块 |
| AI | 问答溯源为主线 **+ 一个可演示的多步 agent 流程**（候选：合同审查自动流程 读合同→检索法条→列风险→起草建议）；多步编排复用现有 LangGraph `workflow_nodes` |
| 并发 | 亮点：**agent 运行幂等 + 可取消 + 工具调用幂等**（复用 `app/models/idempotency.py`）——重复提交/刷新/重试不重复执行或串结果、生成中断不泄漏；乐观锁（document `version_id_col`）维持现状 |
| 评测 | 真实跑分 + 交叉模型三层评审（规则 → deepseek-v4.1-flash → 人工抽检分歧）**+ 接入 CI 回归门禁**（`.github/workflows/ci.yml` 已存在）：改 prompt/参数自动跑评测防回归 |
| 可观测 | **自建"LLM 链路 + token 成本 + 延迟"面板**，复用 `llm_analytics` + OTel `observe_span` + `cost_ledger`，把每次问答的检索/生成/裁判成本与耗时归因；不引入外部 trace 工具 |

**测试基调**：新代码配单测；不设全仓覆盖率门禁（延续 pyproject 现状），但关键路径（agent 幂等 / 评测 / 检索）进 CI 回归。

**待实现时再定（非阻塞）**：多步 agent 的具体演示流程脚本；copilot 面板信息密度微调；视觉 2-3 套风格方案。




