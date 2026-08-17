# 律智检 · 架构重构方案与实施计划

> 状态：分析完成，待确认第一步后进入渐进式实施
> 作者：资深软件架构师（AI 协作）
> 范围：`app/`（FastAPI + SQLAlchemy 后端）+ `frontend/`（Vue3 + Element Plus）

---

## 0. 结论速览（TL;DR）

| 维度 | 现状 | 结论 |
|---|---|---|
| 规模 | 后端 250 个 `.py` / ~51k 行；前端 69 个源文件 | 中型单体，可维护性已达临界点 |
| 结构 | `services/` 95 个文件**平铺**、`api/` 33 个文件平铺 | **最核心问题**：无领域分层，内聚差 |
| 循环依赖 | **4 处环**（最严重为 5 节点环） | 需优先打破 |
| 上帝文件 | 后端 5 个 >1000 行；前端 4 个视图 >1400 行 | 职责混杂的集中表现 |
| 孤儿代码 | `token_api.py`、`oplog_api.py` 未挂载 | 死路由，需决策 |
| 配置 | `core/config/` 已 14 文件模块化 | 基础良好，仅需查漏 |
| 结论 | **不必推倒重来**，走「模块化单体 + 有界上下文」渐进式重构 | 行为保持 + 小步提交 |

---

## 1. 整体评估（现状盘点）

### 1.1 功能清单（按有界上下文归类）

当前功能实际分布如下（模块前缀来自 `app/services`、`app/api`、`app/models`、`app/core`）：

| 有界上下文 | 主要能力 | 关键模块 |
|---|---|---|
| 认证与账号 | 登录/注册/JWT、MFA、企业 SSO/LDAP、账号注销、访问令牌 | `auth_*`, `user_auth_service`, `mfa_service`, `enterprise_auth_service`, `auth_token_service`, `account_deletion_service` |
| 组织与权限 | 组织/成员、RBAC、数据权限、API Key、安全审计 | `org_*`, `authorization_service`, `data_permission_service`, `api_key`, `security_audit_service` |
| 对话与记忆 | 聊天、会话记忆、WebSocket 会话 | `chat_*`, `conversation_memory_service`, `memory_api`, `ws_session_service` |
| 文档 | 文档 CRUD、解析、索引、流水线、QA、冲突、治理、安全、任务 | `document_*`（约 14 个）、`analysis_service`, `archive_service`, `contract_diff_service` |
| 法律工作台 | 案件、审批、计费、合同、领域模型、门户、截止日期 | `legal_*`（约 12 个）、`deadline_service`, `legal_scheduler` |
| RAG / 检索 | 向量检索、重排、查询扩展、缓存、知识图谱、Agentic RAG | `rag_service`, `rag_runtime`, `rag_cache`, `query_expansion`, `rerank`, `vector_store`, `agentic_rag_service`, `legal_retrieval_service`, `legal_knowledge_graph_service`, `document_indexing` |
| Agent | 规划、工作流节点、工具注册、审批、审计、补偿、运行状态 | `agent_*`（14 个）+ `workflows/langgraph_compat` |
| LLM 基础设施 | 网关、路由、限流、结构化输出、可观测 | `core/llm_client`, `core/model_gateway`, `core/model_policy`, `core/llm_provider_adapter`, `core/ollama_client`, `services/llm_service`, `services/llm_governance_service`, `services/llm_observability_service` |
| 计费与订阅 | 订阅、发票、退款、配额、成本台账、对账 | `billing_service`, `subscription_service`, `payment_event_service`, `cost_ledger_service`, `token_service`, `reconciliation_service`, `billing_state_machines` |
| 通知与外联 | 站内通知、模板、邮件、飞书、告警、邮箱同步 | `notification_*`, `outbound_email_service`, `feishu_service`, `operational_alert_service`, `mailbox_sync_service` |
| 集成与连接器 | 连接器同步框架、模拟客户端 | `connector_sync_framework`, `mock_connector_client` |
| 可观测与审计 | 遥测、指标、操作日志、审计导出、日志检索 | `core/telemetry`, `core/observability`, `core/metrics`, `oplog_service`, `audit_log_service`, `audit_export_service`, `log_search_service` |
| 异步任务 | Celery 任务、异步作业、任务运行、幂等 | `app/tasks/*`, `async_job_service`, `task_run_service`, `idempotency_service` |
| MCP | 工具执行、SQL 守卫、权限守卫、工具契约 | `app/mcp/*` |
| Agent 工具 | 文档/法律/SQL/任务工具基类 | `app/tools/*` |

**观察**：功能已经相当完整，但「法律」域被拆成 8 个 API 文件 + 12 个 service 文件平铺，缺乏一个统一的 `legal/` 聚合边界；「Agent」「RAG」「LLM 基础设施」三块逻辑相互渗透，边界模糊。

### 1.2 模块依赖关系（静态导入分析）

- **低层枢纽（健康）**：`core.database`（94 个导入者）、`core.config`（78）、`models.user`（61）、`core.auth`（36）、`core.time`（35）。这些是底座，被广泛依赖是正常的。
- **耦合热点（需治理）**：`app/tasks`（出度 44）——Celery 任务层几乎 import 了所有 service，是最大的耦合中枢；`services/agent_service`（出度 28）、`api/document_api`（20）、`api/legal_platform_api`（19）也都是「上帝模块」的信号。

### 1.3 循环依赖（必须优先处理）

AST 强连通分量扫描发现 **4 处环**：

| # | 环规模 | 成员 | 风险 |
|---|---|---|---|
| C1 | **5 节点** | `core.celery_app` ↔ `services.analytics_service` ↔ `services.document_service` ↔ `services.operational_alert_service` ↔ `tasks` | **最高**：任务层与业务层双向耦合，改任一方都牵动全局 |
| C2 | 2 节点 | `core.observability` ↔ `services.oplog_service` | 中：基础设施反向依赖业务 |
| C3 | 2 节点 | `services.storage_service` ↔ `services.storage_cloud_adapters` | 低：适配器与门面互引用（应单向） |
| C4 | 2 节点 | `services.notification_service` ↔ `services.outbound_email_service` | 中：通知域内循环 |

### 1.4 数据流向（现状）

```
HTTP/WS 请求
   └─► api/*（薄路由，但部分过厚如 legal_portal_api 1037 行）
         └─► services/*（业务逻辑，直接操作 ORM）
               ├─► models/*（SQLAlchemy ORM，40 个平铺文件）
               ├─► core/database|auth|config|time（底座）
               └─► 外部：LLM（core/model_gateway→llm_client）
                     │  向量库（vector_store）│ Redis │ MySQL │ 飞书/邮箱

Agent 流：agent_service → agent_planner → agent_workflow_nodes
           → tools/* → mcp/executor → llm_client
RAG 流：document_service → document_parsing/indexing → vector_store
           → rag_service → rerank/query_expansion → llm_service

异步流：api 创建任务 → Celery（app/tasks）→ services → 状态写回 task_status/WS
```

**核心问题**：`api → service → model` 三层清晰，但 `services` 这一层内部**没有子结构**，导致「业务层」和「基础设施层」混在一起（例如 `storage_service`、`vector_store`、`feishu_service` 这类基础设施与 `legal_service`、`billing_service` 这类领域服务同级平铺）。

### 1.5 冗余逻辑与坏味道清单

1. **上帝文件**（>1000 行，职责混杂）：`rag_service.py`(1474)、`analytics_service.py`(1465)、`agent_service.py`(1457)、`tasks/__init__.py`(1299)、`document_service.py`(1078)、`legal_portal_api.py`(1037)、`legal_platform_api.py`(1012)。
2. **孤儿路由**：`app/api/token_api.py`、`app/api/oplog_api.py` 定义了真实端点但**未在 `main.py` 挂载** → 死代码。
3. **LLM「治理」概念重复**：`core/llm_governance.py` 与 `services/llm_governance_service.py` 并存，职责边界不清。
4. **RAG 碎片化**：约 10 个文件分散实现检索能力，缺少统一「检索门面」。
5. **`workflows/` 近乎空壳**（仅 `langgraph_compat.py`），而 Agent 编排逻辑实际散落在 14 个 `services/agent_*.py`。
6. **命名冲突**：仓库根 `tasks/`（存 PRD markdown）与 `app/tasks/`（Celery 代码）同名，易混淆。
7. **垃圾目录**：仓库根 `MagicMock/` 下有个目录名叫 `get_settings().DATABASE_URL.removeprefix()`（测试副作用产生的脏数据），应删除并加 `.gitignore` 防护。
8. **前端上帝视图**：`System.vue`(1921)、`Documents.vue`(1849)、`Agent.vue`(1560)、`LegalWorkspace.vue`(1417)、`Tasks.vue`(931) —— 一个视图承载了列表、详情、弹窗、轮询、WebSocket 全部职责。

---

## 2. 目标架构设计

### 2.1 架构风格：模块化单体（Modular Monolith）+ 有界上下文

**不引入微服务、不做完整 DDD 重写。** 保持单一 FastAPI 进程，但把「按层平铺」改为「按业务域纵向切片」，域内再分层。目标：每个域一个包，包内 `api / service / model` 内聚，域间只通过**公开接口**（service 门面）通信。

### 2.2 目标目录结构

```
app/
├── api/                        # 表现层：HTTP/WS 路由（薄，只做参数校验+调用 service）
│   ├── auth/                   #   auth_api, account_deletion_api
│   ├── org/                    #   org_api, org_member_api
│   ├── legal/                  #   legal_*, legal_billing, legal_portal, ...（8 文件聚合）
│   ├── documents/              #   document_api, document_conflict_api
│   ├── billing/                #   subscription_api, platform_payment_api
│   ├── agent/                  #   agent_api, mcp_api
│   ├── admin/                  #   dashboard_api, analytics_api, prompt_api, api_key_api
│   ├── channels/               #   feishu_api, miniapp_api, outbound_api
│   └── open/                   #   legal_platform_api.open_router（开放平台）
│
├── services/                   # 业务层：按有界上下文分子包（本轮核心改造）
│   ├── auth/
│   ├── org/
│   ├── legal/
│   ├── documents/
│   ├── billing/
│   ├── agent/
│   ├── rag/                    #   检索统一门面
│   ├── notification/
│   ├── observability/
│   └── integration/            #   connector / mailbox / feishu
│
├── models/                     # 数据层：ORM，按域分包子包（models/legal/*.py 等）
├── repositories/               # （渐进引入）数据访问 seam，先不动现有 service 直查 ORM
├── schemas/                    # Pydantic DTO（已按域拆好，维持）
├── core/                       # 横切基础设施（保持，仅清理 LLM 治理重复）
│   ├── config/                 #   已模块化，保留
│   ├── db/  llm/  security/  observability/  time/  errors/
├── mcp/                        # MCP 工具执行（维持）
├── tools/                      # Agent 工具（维持）
└── tasks/                      # Celery（本轮先拆环，后续瘦身 __init__.py）

frontend/src/
├── api/                        # 已按域分好，维持
├── composables/                # 已按关注点分好，维持
├── views/                      # 上帝视图拆分：拆成 views/<domain>/ + components/<domain>/
├── components/                 # 领域组件下沉
├── stores/                     # （建议）Pinia store 集中，替代视图内散落的 ref 状态
└── utils/                      # 维持
```

### 2.3 分层依赖规则（红线）

```
api ──► service ──► model/repository ──► core（底座）
        ▲  ▲
        │  └──► infrastructure adapter（llm/storage/messaging/cache）
        │
        └────── 域间只通过 service 门面，禁止跨域 import 内部实现
```

- **禁止**：`core` 反向 import `services`（这正是环 C2 的来源）。
- **禁止**：`models` import `services`（当前应无，作为约束固化）。
- **禁止**：`api` 直接写业务逻辑（部分 API 当前过厚，逐步下沉）。
- **允许**：`tasks` 依赖 `services`（异步层调用业务），但 `services` **绝不** import `tasks`/`celery_app`（拆环 C1 的关键）。

---

## 3. 代码规范统一方案

### 3.1 命名规范

| 对象 | 后端（Python） | 前端（JS/Vue） |
|---|---|---|
| 模块/文件 | `snake_case.py` | `camelCase.js` / `PascalCase.vue` |
| 类 / 组件 | `PascalCase` | `PascalCase` |
| 函数 / 方法 | `snake_case` | `camelCase` |
| 变量 / 常量 | `snake_case` / `UPPER_SNAKE` | `camelCase` / `UPPER_SNAKE` |
| Service 单例 | `xxx_service`（模块级单例） | — |

### 3.2 注释 / 文档字符串

- 统一 **Google 风格 docstring**：模块头部一行说明 + 类/函数 `Args/Returns/Raises`。
- 业务分叉处保留「为什么」注释，禁止「做了什么」的废话注释、禁止注释掉的死代码。
- 每个 service 门面类顶部写一句职责边界（一句话说明「它不负责什么」）。

### 3.3 错误处理

- **统一**：业务异常抛 `app/core/error_codes.py` 中已注册的类型化异常（现有错误码注册表继续作为契约门）。
- **禁止**裸 `except:`；`except Exception` 必须 re-raise 或转换为业务异常。
- API 层不再手写 `HTTPException` 与错误码字符串，统一由全局 handler + 类型化异常映射。

### 3.4 日志

- **结构化日志**：携带 `request_id`（来自 `core/obs_context`），统一格式。
- 分级纪律：`DEBUG`（细节）/ `INFO`（关键业务节点）/ `WARNING`（可恢复异常）/ `ERROR`（需人工）。
- **禁止** `print()`（后端）与 `console.log()` 残留在生产路径（前端用统一 logger）。

### 3.5 工具链（建议立即落地）

| 层 | 格式化 | 静态检查 | 类型 |
|---|---|---|---|
| Python | Ruff（format + lint 二合一） | Ruff | mypy（渐进，先 `basic` 模式） |
| 前端 | Prettier | ESLint | Vue TS 逐步开启 |
| 提交 | `pre-commit`（ruff + prettier + eslint-staged） | — | — |

> 已有 `.ruff_cache`，说明 Ruff 已接触但未固化为强制门禁；本轮将其落到 `pyproject.toml` + `pre-commit`。

---

## 4. 模块拆分与合并计划

### 4.1 合并（消除重复）

1. **LLM 治理合并**：`core/llm_governance.py` + `services/llm_governance_service.py` → 单一 `core/llm/governance.py`（或 `services/llm/governance.py`），保留一个门面。
2. **RAG 统一门面**：`rag_service / rag_runtime / rag_cache / query_expansion / rerank / vector_store / document_indexing` → `services/rag/` 包，对外只暴露一个 `RetrievalService` 门面。
3. **Agent 工具注册去重**：`agent_registry / agent_skill_registry` 若职责重叠则合并为一个注册表。

### 4.2 拆分（消除上帝文件）

| 文件 | 拆分方向 |
|---|---|
| `rag_service.py`(1474) | 拆「检索 / 生成 / 缓存 / 引用装配」 |
| `analytics_service.py`(1465) | 拆「漏斗 / 令牌 / 组织 / 知识库」分析器 |
| `agent_service.py`(1457) | 拆「运行编排 / 审批 / 补偿 / 状态」 |
| `tasks/__init__.py`(1299) | 拆为 `tasks/<domain>_tasks.py` 多个文件，`__init__` 只做注册 |
| `document_service.py`(1078) | 拆「CRUD / 解析调度 / 权限 / 版本」 |
| `legal_portal_api.py`(1037) | 拆「门户 / 进度 / 通知」子路由 |
| `System.vue`(1921) | 拆「观测 / 审批 / 保留策略 / 知识库」页签组件 |
| `Documents.vue`(1849) | 拆「上传 / 分析 / 版本 / QA」面板组件 |
| `Agent.vue`(1560) | 拆「运行面板 / 审批面板 / socket 逻辑」 |
| `LegalWorkspace.vue`(1417) | 拆「案件 / 审批队列 / 咨询」页签 |

---

## 5. 配置与环境管理

**现状已良好**：`core/config/` 已拆 14 个文件（base/database/llm/legal/rag/security/storage/…），`.env.example` 齐全，`ENVIRONMENT` 区分 development/pilot/production，`validate_production_or_raise()` 做生产强校验。

**本轮补齐**：
1. 新增 `.env.test`（供 CI/pytest 隔离，SQLite + 假外部服务）。
2. 扫描并清零残留硬编码字面量（如散落的默认超时、魔法数字）→ 收敛进 config。
3. 加 lint 规则：禁止 `os.environ[...]` 散落（统一走 `get_settings()`）。
4. 敏感项（`LEGAL_DATA_ENCRYPTION_KEY` 等）已强制独立配置，维持现状并补 CI 校验。

---

## 6. 文档输出

1. **README.md 重写**（新目录结构、启动方式、核心模块地图、领域边界说明）。
2. **`docs/ARCHITECTURE.md`**：分层规则、有界上下文清单、依赖红线、本 ADR。
3. **`docs/adr/`**：关键架构决策记录（模块化单体 vs 微服务、RAG 门面、LLM 治理合并等）。
4. **接口文档**：维持 OpenAPI 自动生成（`main.py` 已内置契约注入），重构阶段用「契约门测试」防回归，不手写文档。
5. **类图/数据流图**：在 `docs/ARCHITECTURE.md` 用 Mermaid 补 2~3 张核心图（分层依赖图、Agent 流、RAG 流）。

---

## 7. 渐进式实施路线图（优先级排序）

> 原则：**每步行为保持、可随时回滚、小步提交、测试先行**。优先级按「风险 × 收益」排序。

| 阶段 | 内容 | 风险 | 收益 | 依赖 |
|---|---|---|---|---|
| **P0 工程卫生 + 工具链** | 清垃圾目录、决策孤儿路由、改命名冲突、落 ruff/prettier/pre-commit | 极低 | 建立基线 | 无 |
| **P1 打破循环依赖** | 拆 C1~C4 四环（重点 C1 任务层解耦） | 中 | 高 | P0 |
| **P2 服务层按域分包** | `services/` 95 文件 → 10 个域子包（纯 import 路径迁移） | 低~中 | 高 | P1 |
| **P3 上帝文件拆分** | 后端 7 个 + 前端 5 个大文件 | 中 | 高 | P2 |
| **P4 去冗余** | 合并 LLM 治理、RAG 门面、Agent 注册表 | 中 | 中 | P3 |
| **P5 前端状态上收** | 引入 Pinia，视图内散状态 → store | 中 | 中 | P3 |
| **P6 文档落地** | README/ARCHITECTURE/ADR/Mermaid | 极低 | 中 | 可并行 |

---

## 8. 第一步详细方案（P0：工程卫生 + 工具链基线）

> 选择 P0 作为第一步的原因：它是后续所有重构的**安全地基**——零行为改动、立即可验证、一次性消除仓库里的明显脏数据，并让格式化/静态检查门禁先行落地，避免后续重构把风格漂移放大。

### 8.1 小步提交拆解（每个 commit 均可独立通过 CI）

1. `chore: 删除测试副作用垃圾目录 MagicMock/ 并加入 .gitignore`
   - 删除 `MagicMock/`（含 `get_settings().DATABASE_URL.removeprefix()` 子目录）。
   - `.gitignore` 增加防护规则（禁止 `MagicMock*` 之类的测试脏目录）。

2. `chore: 迁移根 tasks/ 下的 PRD 文档到 docs/prd/`
   - 根 `tasks/prd-client-portal-p1.md` → `docs/prd/prd-client-portal-p1.md`。
   - 消除与 `app/tasks/` 的命名冲突。

3. `chore: 引入 ruff + pre-commit 工具链`
   - 新增/完善 `pyproject.toml`（ruff format + lint 配置，line-length 120，排除 migrations）。
   - 新增 `.pre-commit-config.yaml`（ruff、prettier、eslint-staged）。
   - 新增 `requirements-dev.txt` 补 ruff/pre-commit（若缺）。
   - **首轮只配置、不批量改代码**，避免巨大 diff。

4. `feat(api): 决策孤儿路由（token_api / oplog_api）`
   - **方案 A（推荐）**：在 `main.py` 挂载 `token_api`（前缀 `/api/usage`）与 `oplog_api`（前缀 `/api/oplog`），补齐漏挂载的既有功能。
   - **方案 B**：若确认不需要，删除两个文件 + 对应 service 的死方法。
   - 需用户拍板（见问题 2）。

5. `docs: 新增 ARCHITECTURE.md 基线（目标结构 + 依赖红线 + 本计划）`
   - 落地本文件的精简版，作为后续阶段的「对照图」。

### 8.2 第一步验收标准

- [ ] `ruff check .` 无 Fatal（E/F 级）新增告警（存量告警先 `--statistics` 记录基线）。
- [ ] 后端全量回归测试通过（现有 pytest）。
- [ ] 前端 `npm run build` 通过（在本地沙箱外执行）。
- [ ] 启动服务，`/api/health` 与受影响路由可用。

### 8.3 第一步不改动的内容（明确边界）

- **不动**任何 service 内部实现（不拆环、不拆上帝文件）。
- **不动**数据库 schema、不新增 migration。
- **不动**对外 API 契约（除挂载两个孤儿路由这一新增项）。

---

## 附：后续阶段的风险预案

- **P1 拆环 C1**：核心手段是让 `services/*` 不再 import `app/tasks` / `core/celery_app`。Celery 任务需要调用的业务逻辑，通过「延迟 import / 依赖注入 / 事件发布」三选一解耦；建议先给 `tasks/__init__.py` 瘦身（拆成 `tasks/<domain>_tasks.py`），把「任务入口」与「业务实现」分离，环自然消失。
- **P2 分包**：用 `git mv` 纯移动 + 一次性更新 import（脚本辅助），不混合任何逻辑修改，保证可 review。
- **P3 拆上帝文件**：每个文件拆前先补「表征测试」（characterization test）锁住当前行为，再逐方法迁移。
