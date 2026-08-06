# 律智检｜法律文书与合同审查智能体平台

基于 FastAPI、Vue、Chroma / Qdrant、Neo4j、Celery 和千问兼容 API 的法律辅助工作台。平台聚焦三条核心业务线：

- **法律咨询辅助**：案情分类、事实整理、法源定位、一般处理建议
- **合同智能审查**：条款风险识别、原文证据定位、修改建议、冲突核对
- **法律文书草稿**：劳动仲裁、民间借贷、消费纠纷、补充协议四类模板，字段校验与待补事实标注

## 产品定位

面向个人用户与小型律所的法律检索、合同审查和文书辅助平台。通过 Agentic RAG、多智能体协作和律师审核机制，降低法律依据定位与关键事实遗漏风险。

**重要边界**：平台定位为法律辅助工具，不提供自动法律结论或律师替代服务。所有输出均标注"AI 辅助结果"，高风险事项强制进入律师审核队列。

## 核心特性

### 1. 法律咨询辅助

- 问题自动分类：劳动争议、合同纠纷、民间借贷、消费纠纷、其他
- 已知事实与待补事实分离，不编造关键信息
- 法规检索与引用定位，支持法规、司法解释、公开案例摘要
- 高风险提示：刑事、人身损害、时效临近、证据不足自动标记
- 多轮追问支持，保留上下文

### 2. 合同智能审查

- 八类条款风险识别：付款、交付、违约、赔偿、保密、知识产权、终止、争议解决
- 原文证据定位，展示段落、页码、条款编号
- 风险等级分层：高 / 中 / 低 / 待补充事实
- 合同对比功能：核对签订日期、金额、责任方、违约条款等 10 项关键字段
- 支持 PDF、DOCX、图片合同上传与 OCR 解析

### 3. 法律文书草稿

支持四类文书模板：
- 劳动争议仲裁申请书
- 民间借贷纠纷起诉状
- 消费纠纷投诉书
- 补充协议

必填字段校验：姓名、金额、日期、地址、请求事项、证据材料不允许留空或自行编造，缺失项明确标记【待补充】。

### 4. 律师审核闭环

- 审核队列：待审核咨询、合同、文书草稿
- 四类审核动作：通过、退回补充事实、转线下咨询、归档
- 审核记录留痕：审核人、时间、意见、版本
- 权限控制：仅管理员和审核律师可执行审核动作
- 审核反馈回流：通过/退回决策落库，用于生成质量评测闭环（AI-2）

### 5. 飞书插件（企业自建应用）

平台核心能力通过飞书机器人复用，飞书端仅做适配层，后端零新功能：

- **M1 单聊咨询**：@插件发文本 → 咨询分类卡片（风险等级/法条引用/待补充事实/追问入口）
- **M2 合同初筛**：单聊发 `.pdf/.docx` → 风险条款卡片 + 深度审查入口
- **M3 文书生成 + 审核队列**：模板表单 → 草稿卡片；"待审核"命令 → 逐项卡片 → 通过/退回回写 Web 审核队列
- **M4 提醒管线**：每日 09:00 beat 任务 → 激活引导卡 / 周报回访卡
- 回调安全：AES-256-CBC 事件解密 + HMAC 签名校验（V2/V1 兼容，`FEISHU_CALLBACK_VERIFY=auto`）

接入指南见 [docs/feishu-app-integration-guide.md](docs/feishu-app-integration-guide.md)。

### 6. 客户门户（POC 交付）

面向客户的只读门户，按组织隔离：

- 链接时效：默认 30 天过期，拒绝访问可追踪
- 品牌化：律所 logo + 自定义欢迎语
- 访问行为分析：去重访客 / 重复访问 / 活跃天数 / 时段分布（并入周报口径）
- 客户反馈：👍/👎 + 待改进说明
- 账单对账：账单摘要 / 已收 / 应付款日

### 7. 订阅与计费

- 三级计划：free / pro / team，额度与上限配置化（`FREE_PLAN_*_QUOTA`）
- 升级意图埋点（`upgrade_intent` oplog），支撑 M-3 转化 A/B 实验
- 对公转账支付流程 + 发票快照；Stripe webhook HMAC 验签（可选）
- 过期自动流转、发票逾期扫描、订阅到期扫描（beat 任务）

## 安全与合规（等保二级对标）

平台按 GB/T 22239-2019 第二级要求实现并自评（见 docs/etc-protection-poc-self-assessment.md）：

| 域 | 实现 |
|---|---|
| 身份鉴别 | 密码 bcrypt + 全站 HTTPS/TLS + JWT；客户门户 OTP 一次性验证码 |
| 访问控制 | 角色四级 RBAC + 资源级鉴权 + 门户按组织隔离 |
| 数据保密 | 敏感字段 AES-256-GCM 静态加密（独立密钥）；LLM 出站 PII 脱敏；邮件 DLP |
| 安全审计 | 操作/审计/登录双轨日志 + 集中检索 `/api/admin/logs/search` + 结构化 JSON 导出（`STRUCTURED_LOG_JSON_LINES`） |
| 备份恢复 | Celery beat 每日 02:00 全量备份 + SHA256 校验 + 异地副本（`BACKUP_OFFSITE_DIR`）+ 保留策略（`BACKUP_RETENTION_COUNT`） |
| 登录防护 | 5 次失败锁定 30 分钟 |
| 合规材料 | 隐私政策/用户协议/供应商清单/数据保留 SLA/制度汇编/应急预案/任命文件（草案，待法务确认） |

## 可观测性

- **Sentry**：前端与 API 错误上报（`SENTRY_DSN`，可留空关闭）
- **OpenTelemetry**：FastAPI 请求 + SQLAlchemy 链路追踪 → OTLP collector（`OTEL_ENABLED` + `OTEL_EXPORTER_OTLP_ENDPOINT`，`https://` 前缀自动启用 TLS）
- **告警**：Webhook 告警（`ALERT_WEBHOOK_URL`）+ 模型路由健康检查
- **成本核算**：按 token 用量记账，日额度/速率治理

## 技术架构

### 后端技术栈

- **FastAPI**：异步 API 服务
- **SQLAlchemy + MySQL / PostgreSQL / SQLite**：元数据与业务台账（生产建议 MySQL/PostgreSQL）
- **Celery + Redis**：异步任务与计划任务（beat：每日备份、提醒、到期扫描、告警等 15+ 计划任务）
- **Chroma（默认）/ Qdrant**：向量检索
- **千问 / Ollama**：LLM 推理与向量化（qwen-plus 主模型 + 简单请求路由小模型）
- **OpenTelemetry + Sentry**：链路追踪与错误上报（可选开关）
- **AES-256-GCM**：敏感字段静态加密

### 前端技术栈

- **Vue 3 + TypeScript**
- **Element Plus**：UI 组件库（按需导入）
- **Vite**：构建工具

### 核心技术机制

- **Agentic RAG**：问题分类 → 查询改写 → 混合检索 → 证据评估 → 有限轮次补检索 → 带引用回答或拒答
- **Graph RAG（可选）**：Neo4j 基于法源修订关系、法律领域和条文关系为已召回候选提供可解释的排序证据；图谱不可用时自动降级至原检索链路
- **多 Agent 协作**：按法律业务领域拆分 Agent（法律咨询、合同审查、法律文书、证据校验），而非技术步骤拆分
- **结构化输出**：使用 JSON Schema 约束合同风险、咨询建议、文书字段和审核动作
- **人机协同**：高风险结论、文书交付和对外动作均进入律师审核或用户确认
- **PII 脱敏**：敏感信息（身份证、手机号、姓名）在送入 LLM 前自动脱敏

## 环境要求

- Python 3.11
- Node.js 20+
- MySQL 8 / PostgreSQL 15+
- Redis 7
- 通义千问 API Key

默认按千问 OpenAI 兼容接口接入。如需本地 Ollama，可把 `LLM_PROVIDER` 切成 `ollama`。

```bash
LLM_PROVIDER=openai_compatible
LLM_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=你的百炼 API Key
LLM_MODEL=qwen-plus
LLM_VISION_MODEL=qwen-vl-max
EMBEDDING_MODEL=text-embedding-v3
VITE_WS_HOST=localhost:8001
```

飞书插件、客户门户、支付网关、Sentry/OTel 均为可选，未配置时对应功能自动降级（见 docs/CONFIG.md）。

## 快速启动

### 1. 安装依赖

```bash
# 后端
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 前端
cd frontend
npm ci
cd ..
```

### 2. 配置环境变量

```bash
# 复制配置模板
cp .env.example .env

# 编辑配置文件，至少配置以下必需项：
# - SECRET_KEY: 至少32字符的强随机密钥
# - LLM_API_KEY: 通义千问API密钥
# - DATABASE_URL: 数据库连接
# - REDIS_URL: Redis连接
# - ADMIN_USERNAME/ADMIN_PASSWORD: 管理员账号

# 运行配置诊断工具检查配置
python scripts/check_config.py
```

**详细配置说明**: 参见 [docs/CONFIG.md](docs/CONFIG.md)

### 3. 初始化数据库

```bash
python scripts/bootstrap_system.py
```

该脚本会执行：
- Alembic 数据库迁移
- 默认 Prompt 模板初始化
- 管理员账号创建
- 演示法律法规与合同模板数据

### 4. 启动服务

```bash
# 后端 API
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

# Celery Worker（新终端）
celery -A app.core.celery_app.celery_app worker --loglevel=info

# Celery Beat（新终端，计划任务）
celery -A app.core.celery_app.celery_app beat --loglevel=info

# 前端（新终端）
cd frontend
npm run dev
```

默认地址：
- 前端：http://localhost:5173
- 后端：http://localhost:8001
- API 文档：http://localhost:8001/docs
- 健康检查：http://localhost:8001/api/health

## Docker Compose 启动

```powershell
docker compose up --build
```

默认会启动 MySQL、Redis、API、Celery Worker、Celery Beat、前端六个服务。

**注意**：
- Docker Compose 会读取项目根目录 `.env` 中的 `LLM_API_KEY`
- 首次启动会拉取基础镜像，耗时取决于网络
- 当前 `frontend` 服务运行的是 Vite dev server，适合本地演示，不是生产静态托管模式

## 核心 API

### 法律咨询

```bash
POST /api/legal/consultations
{
  "question": "我在公司工作了3年，公司突然辞退我，没有支付经济补偿金，我应该怎么办？"
}
```

返回：
- 问题分类（劳动争议、合同纠纷等）
- 已知事实与待补充事实
- 参考法律依据（含法规名称、条文、版本）
- 一般性处理建议
- 风险等级（高 / 中 / 低）

### 合同审查

```bash
POST /api/legal/contract-reviews
{
  "title": "技术服务合同",
  "content": "合同全文..."
}
```

返回：
- 风险清单（条款类型、风险等级、原文定位、修改建议）
- 审查意见总结
- 高风险项数量

### 合同对比

```bash
POST /api/legal/contract-compare
{
  "title_a": "原合同",
  "content_a": "原合同全文...",
  "title_b": "补充协议",
  "content_b": "补充协议全文..."
}
```

返回：
- 10 项关键字段对比（签订日期、金额、责任方、违约条款等）
- 冲突标记与严重程度
- 对比总结

### 法律文书草稿

```bash
POST /api/legal/drafts
{
  "document_type": "labor_arbitration_application",
  "fields": {
    "申请人": "张三",
    "被申请人": "某公司",
    "仲裁请求": "支付经济补偿金3万元",
    "事实与理由": "...",
    "证据清单": "劳动合同、工资流水"
  }
}
```

返回：
- 文书草稿全文
- 缺失字段清单（必填字段未填时标记为 needs_facts）
- 参考法律依据

### 律师审核

```bash
GET /api/legal/review-queue
# 返回待审核的咨询、合同、文书列表

POST /api/legal/review-queue/{target_type}/{target_id}/actions
{
  "action": "approve | return | offline | close",
  "note": "审核意见"
}
```

## 评测与质量保证

### 评测体系

项目内置多层评测（`eval/` 目录 + CI 评测回归门禁）：

- **生成质量评测**：咨询/合同审查/文书字段校验样例（`eval/bundles/demo_legal/`），含引用完整率、拒答准确率、事实完整性
- **法律检索评测**：`run_legal_retrieval_eval.py`（法源召回）
- **混合检索 / Graph RAG 评测**：`run_hybrid_retrieval_eval.py` / `run_graph_rag_eval.py`
- **审核反馈回流评测（AI-2）**：`run_generation_eval --review-feedback`，用真实/模拟审核决策回归生成质量
- **模型对比（AI-6）**：`compare_models.py` / `run_experiments.py`
- **语料评测集（AI-1）**：`eval/bundles/` 冻结题集 + `export_real_corpus_eval.py` 换真实语料

生成真实业务评测集：

```bash
python eval/create_eval_bundle.py --bundle-name real_legal_q3 --pretty
python eval/index_eval_corpus.py --bundle-dir eval/bundles/real_legal_q3 --pretty
python eval/run_eval.py --bundle-dir eval/bundles/real_legal_q3 --user-id 9000 --pretty
```

### 核心指标

- **引用完整率**：咨询建议和合同风险是否附带有效法源引用
- **拒答准确率**：无依据、高风险、证据不足时是否正确拒答或转人工
- **事实完整性**：文书生成对关键字段缺失是否明确提示，不自行编造
- **律师审核率**：高风险咨询和合同是否正确进入审核队列
- **缺失条款召回**：合同审查对常见缺失条款的召回率（cr_006 专项）
- **北极星与留存**：活跃律师数、7/30 日留存（/api/admin/north-star、/api/admin/retention）
- **成本**：LLM 用量按动作核算，日额度/速率治理

## 运营工具（scripts/）

| 脚本 | 用途 |
|---|---|
| `pilot_weekly_report.py` | 试点周报：漏斗/留存/成本/NPS/门户行为（排除供给账号） |
| `evaluate_ab_conversion.py` | M-3 转化 A/B 判定（χ² 显著 + ≥30% 提升 + D7 留存，样本≥30） |
| `create_pilot_backup.py` | 每日全量备份（DB + 数据目录 + SHA256 + 异地副本 + 保留策略） |
| `check_openapi_contract.py` | OpenAPI 契约快照一致性门禁 |
| `export_review_feedback.py` / `export_exit_surveys.py` | 审核反馈 / 退出问卷导出 |
| `loadtest_legal_paths.py` | 主路径压测 |
| `check_pilot_readiness.py` | 试点环境门禁自检 |

## 运营看板

访问 `/api/legal/metrics` 可获取：
- 总咨询数、合同审查数、文书草稿数
- 引用完整率（有法源引用的咨询占比）
- 草稿采纳率（律师通过的草稿占比）
- 高风险咨询数、高风险合同数
- 退回原因统计
- 审核状态分布

## 演示建议路径

1. 打开"法律咨询"，输入劳动争议问题，观察问题分类、事实整理、法源引用
2. 打开"合同审查"，上传合同或粘贴合同文本，查看风险清单和原文定位
3. 打开"文书草稿"，选择"劳动仲裁申请书"，填写必填字段，生成草稿
4. 打开"律师审核"，查看待审核队列，执行通过/退回动作
5. 打开"系统中心 → 法律运营看板"，查看引用完整率、草稿采纳率、高风险统计

## 交付核验

推荐在交付前至少做这几步：

1. `python -m pytest -q`（全量测试，当前 742 项通过）
2. `ruff check --select E9,F821,F823,F632,F706,F811 app scripts tests`（CI 静态门禁）
3. `python scripts/check_openapi_contract.py`（OpenAPI 契约快照一致性）
4. `npm run build`（在 `frontend/` 下执行）
5. `docker compose config`
6. `docker compose up --build`

## 项目结构

```
├── app/                    # 后端代码
│   ├── api/                # API 路由（legal/feishu/portal/subscription/dashboard/admin…）
│   ├── services/           # 业务逻辑（legal_service / feishu_service / prompt_service…）
│   ├── models/             # 数据模型（legal / legal_billing / subscription / feishu_binding…）
│   ├── tools/              # Agent 工具
│   ├── tasks/              # Celery 任务（beat 计划任务、备份、飞书提醒）
│   └── core/               # 核心模块（config/auth/telemetry/observability/encryption…）
├── frontend/               # 前端代码（Vue 3 + Element Plus）
│   └── src/
│       ├── views/          # LegalWorkspace / LegalPortal / LegalBilling / Pricing…
│       └── components/legal/
├── eval/                   # 评测（bundle 构建、LLM 评测、法律检索/图谱评测、反馈回流评测）
├── scripts/                # 运维与数据脚本（备份、周报、A/B 判定、契约检查、压测…）
├── docs/                   # 产品/合规/接入文档（含等保自评、飞书接入指南、合规四件套）
├── alembic/                # 数据库迁移版本
├── FL.md                   # 法律平台需求文档
└── README.md               # 本文件
```

## 常见问题

### 1. 如何切换到本地 Ollama？

修改 `.env`：
```bash
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
LLM_MODEL=qwen2:7b
```

### 2. 如何切换向量库到 Qdrant？

修改 `.env`：
```bash
VECTOR_STORE_PROVIDER=qdrant
QDRANT_URL=http://localhost:6333
VECTOR_STORE_COLLECTION_NAME=legal_docs
```

### 3. 如何添加新的法律法规？

访问 `/api/legal/sources`，通过管理后台添加法规、司法解释、公开案例摘要，必须填写：
- 标题、来源类型（statute / case / template）
- 引用格式、管辖区域、版本
- 生效日期、状态（active / inactive）

### 4. 如何自定义文书模板？

修改 `app/services/legal_service.py` 中的 `DRAFT_FIELDS` 和 `DRAFT_REQUIRED_FIELDS`，添加新的文书类型和字段定义。

### 5. 合同审查支持哪些文件格式？

支持 PDF、DOCX、DOC、TXT、MD 格式。PDF 和图片合同会自动触发 OCR 解析。

## 技术支持

- 问题反馈：GitHub Issues
- 技术文档：`/docs` 目录
- API 文档：http://localhost:8001/docs
- 产品需求：FL.md

## 许可证

本项目仅供学习和演示使用，不得用于提供正式法律服务或替代律师执业。
