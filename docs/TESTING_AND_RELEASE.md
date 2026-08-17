# 律智检 · 测试、覆盖率与发布工程手册

> 本文档是测试/评估/发布流程的**单一事实来源**，随阶段实施持续更新。
> 阶段 1（P1 分层测试）起稿；阶段 2-6 将追加契约/韧性/评估/CI/发布章节。

---

## 1. 分层测试约定（阶段 1）

### 1.1 目录分层

`tests/` 下按层新增用例（**既有 137 个平铺测试文件保持原位不动**，避免无关重构）：

| 目录 | 层 | 内容 |
|---|---|---|
| `tests/domain/` | 纯逻辑 | 状态机 / 幂等键 / 期限计算 / 权限判定 / 版本契约等无 IO 纯函数 |
| `tests/service/` | 业务服务 | fake repository / fake client 隔离 DB 与外部依赖 |
| `tests/repository/` | 数据访问 | SQLite 内存库 CRUD、分页/排序/唯一约束/可空边界 |
| `tests/api/` | 路由契约 | TestClient 走 app.main：状态码、错误码、envelope、幂等头、If-Match/409 |
| `tests/task/` | Celery 任务 | 直调任务函数（`.run` / 函数本体），幂等、重试、beat 锁语义 |
| `tests/contract/` | 适配器契约 | （阶段 2）LLM/Redis/向量库/存储/OAuth/支付 调用协议 |
| `tests/resilience/` | 韧性 | （阶段 3）并发/超时/重试/重复消息/乱序/断电恢复 |

### 1.2 测试基建约定（沿用既有风格）

- 框架：`unittest.TestCase` / `unittest.IsolatedAsyncioTestCase`（**不引入 pytest-asyncio**）。
- 数据库：每个测试文件自建 `sqlite+pysqlite:///:memory:`（`StaticPool`）+ `Base.metadata.create_all`。
- API 测试：`TestClient(app)` + `app.dependency_overrides[get_db]` 注入会话（参考 `tests/test_obs_api_p1.py`）。
- 外部服务：一律 mock/fake/内存替身；**禁止依赖真实外部服务**。
  - Redis：测试用内存 stub 或 `fakeredis`（`requirements-dev.txt` 已引入；`tests/task/test_deadline_reminder_task.py`
    用确定性 `RuntimeError` 阻断 redis 连接，验证锁 fail-open 语义）。
- 确定性：`patch` 时间相关调用（`app.core.time.utc_now` 等）避免时钟漂移；避免真实 broker/worker。

### 1.3 新增用例清单（阶段 1 交付）

| 文件 | 覆盖 |
|---|---|
| `tests/domain/test_versioning_contract.py` | If-Match 解析 / ETag 生成契约（含 `v-1` 负版本 fail-closed 边界） |
| `tests/service/test_task_service_due_date.py` | `_parse_due_date` 边界（10 位日期补午夜 / ISO / 非法与空值） |
| `tests/service/test_token_service.py` | 成本计算（Decimal/未配置/畸形定价）、用量记录 + 成本台账、统计聚合 |
| `tests/repository/test_repository_query_boundaries.py` | 分页 / 稳定排序 / 状态过滤 / 唯一约束 / 可空列 / version 自增 |
| `tests/api/test_if_match_endpoints_409.py` | `_IF_MATCH_ENDPOINTS` 名单枚举：声明一致性与三端点 409 行为 |
| `tests/task/test_deadline_reminder_task.py` | 期限提醒窗口（offset 计算）、去重幂等、状态过滤、时区统一 |
| `tests/task/test_document_task_orchestration.py` | parse/chunk/index/summarize/analyze/recover/export 任务编排直调（skipped/degraded/永久错误/重试分支） |
| `tests/task/test_task_retry.py` | 重试上限 / 指数退避 / 文档进度语义（注入 mock，不触真实 DB） |

阶段 1 过程中经用户批准修复的既有契约缺陷：`main.py::_IF_MATCH_ENDPOINTS` 中 org 路径
`/api/organizations/{org_id}` → 真实路由 `/api/org/organizations/{org_id}`，并重基线
`docs/openapi-snapshot.json`（`python scripts/check_openapi_contract.py --update`）。

## 2. 覆盖率（阶段 1）

### 2.1 配置（pyproject.toml）

- `[tool.pytest.ini_options]`：`testpaths = ["tests"]`（不加全局 `--cov`，按需启用）。
- `[tool.coverage.run]`：`source = ["app"]`、`branch = true`。
- `[tool.coverage.report]`：`show_missing = true`。

### 2.2 门槛口径

| 范围 | 阈值 | 说明 |
|---|---|---|
| 全量（app/） | ≥60% | 本地硬验命令见下；CI 仅出报告不硬失败（人工把关） |
| 关键路径 | ≥80% | 幂等 / 乐观锁 / 支付 / outbox / 契约模块：`app.core.versioning`、`app.core.external_resilience`、`app.core.circuit_breaker`、`app.services.jobs`、`app.services.billing`、`app.services.notification`、`app.tasks` |

### 2.3 现状与追踪（阶段 3 结束快照）

| 指标 | 阶段1 | 阶段3 | 目标 | 状态 |
|---|---|---|---|---|
| 全量覆盖率 | 71% | ~71% | ≥60% | ✅ 达标 |
| 关键路径覆盖率 | 64.94% | **66.47%** | ≥80%（DoD#2，阶段 1–3 合计） | 🔶 未达标（差距主要在后三类模块） |

```bash
# 阶段 3 结束时的模块缺口 Top5（按缺失语句数）
#   app/services/billing/billing_service.py          50%  (缺 251 句)
#   app/services/notification/notification_service.py 52%  (缺 169 句)
#   app/services/jobs/task_service.py                46%  (缺 137 句)
#   app/tasks/legal_tasks.py                         29%  (缺 187 句)
#   app/tasks/ops_tasks.py                           24%  (缺 101 句)
# 处置：见阶段 3 报告「待决策点」——继续补测 or 收窄关键路径口径（需用户批准）
```

### 2.3 命令

```bash
# 全量覆盖报告（终端 + 缺失行标注）
python -m pytest tests/ --cov=app --cov-report=term-missing

# 全量硬验（≥60%）
python -m pytest tests/ --cov=app --cov-fail-under=60

# 关键路径硬验（≥80%）
python -m pytest tests/ --cov=app.core.versioning --cov=app.core.external_resilience \
  --cov=app.core.circuit_breaker --cov=app.services.jobs --cov=app.services.billing \
  --cov=app.services.notification --cov=app.tasks --cov-fail-under=80

# XML 报告（CI 工件）
python -m pytest tests/ --cov=app --cov-report=xml
```

## 3. 每阶段回归门禁（Definition of Done）

每阶段结束必须全部通过，方可进入下一阶段：

```bash
python -m pytest tests/ -q                              # 后端全量（不减少既有通过数）
cd frontend && npm run build                            # 前端构建
cd frontend && npx eslint src                           # 前端静态检查（7 error + 54 warning 为已接受基线）
cd frontend && npm run test:e2e                         # Playwright e2e（需后端环境）
python scripts/check_openapi_contract.py                # OpenAPI 契约（改契约后必须）
```

## 4. 变更纪律（红线）

1. 不改业务逻辑 / API 契约 / schema / 迁移历史；测试暴露真实缺陷时**先单独报告并获同意**再修。
2. 新增测试必须独立、可重复、不依赖真实外部服务（mock/fake/内存替身）。
3. 新增依赖须申报：改 `requirements-dev.txt` 后用
   `uv pip compile --no-header --python-platform linux --python-version 3.11 --generate-hashes -o requirements-dev.lock requirements-dev.txt requirements.txt`
   重编译锁文件（CI supply-chain 校验锁新鲜度）。
4. 前端 ESLint 基线（7 error + 54 warning）为已接受状态，不为消除告警改视图命名。

## 5. 阶段 4–6 交付导览（详见分文档）

| 阶段 | 交付 | 文档 |
|---|---|---|
| 4 评估治理 | PII 脱敏（eval/redact.py + redact_check.py）、分层采样（stratified_sampler.py）、seed 可复现（--seed）、延迟 P50/P95 指标 | docs/EVAL_METRICS.md |
| 5 CI 强化 | migration-check（scripts/check_migrations.py）、frontend-audit（npm audit --audit-level=critical）、perf-smoke（scripts/perf_smoke.py）三个新 job | 本文件 §6 + ci.yml |
| 6 发布流程 | Feature Flag（app/core/feature_flags.py + tests/test_feature_flags.py）、Canary/回滚迁移规范 | docs/CANARY_AND_RELEASE.md |

### §6 CI 新增 job 本地复现命令（阶段 5，截止 79.3% 覆盖率快照）

```bash
python -B scripts/check_migrations.py            # migration-check（离线，不长连接 DB）
cd frontend && npm audit --audit-level=critical  # frontend-audit（high 为存量告警，critical 阻断）
python -B scripts/perf_smoke.py                  # perf-smoke（/、live、ready，阈值 3000ms）
```

