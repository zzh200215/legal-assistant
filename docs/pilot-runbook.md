# 小团队试点运行手册

本手册适用于 3-10 名内部律师或企业法务试用。试点仅开放案件、法规知识库、合同审查、人工审核、关键日期和受控文件门户；支付、电子签署和开放 API 不作为试点前提。

> 运营侧（招募话术 / 30s demo / 7 天目标 / 退出问卷 / 周报口径）见 `pilot-success-playbook.md`。

## 启动前

1. 复制 `.env.example` 为 `.env`，设置 `ENVIRONMENT=pilot`。
2. 配置 MySQL、Redis、LLM、管理员账号和独立的 `LEGAL_DATA_ENCRYPTION_KEY` 或 `LEGAL_DATA_ENCRYPTION_KEYS_JSON`。不要使用 SQLite、示例密钥或 JWT `SECRET_KEY` 替代法律数据加密密钥。可将 `LLM_SMALL_MODEL` 配置为当前账号可用的千问小模型以承担短文本请求；上线前在目标账号核验模型名称、免费额度和限流，不要将 API Key 写入文档或截图。
3. 保持 `PAYMENT_CHECKOUT_BASE_URL`、`SIGNING_FADADA_SANDBOX_URL` 和 `SIGNING_FADADA_API_KEY` 为空。Neo4j 与 SMTP 不是内部试点的启动前置。
4. 运行预检：

```powershell
python scripts/check_pilot_readiness.py
```

只有输出 `"ready": true` 才继续启动。

Docker Compose 会将 `.env` 内的主模型、小模型、独立小模型端点/密钥、重试和路由告警阈值同时传入 API 与 Celery Worker。修改这些配置后必须重建或重启这两个服务，不能只重启前端。

## 启动与验证

```powershell
docker compose up --build -d
docker compose ps
Invoke-WebRequest http://localhost:8001/api/health/ready | Select-Object -ExpandProperty Content
```

确认 API、Celery Worker、Celery Beat、MySQL 和 Redis 均处于健康状态。首次启动会执行 `scripts/bootstrap_system.py`；正式试点前应单独执行一次数据库备份和恢复演练。

## 备份与恢复演练

备份脚本从环境变量读取数据库地址，不接受在命令行传入的连接串；生成的 `manifest.json` 只记录数据库类型、主机和库名，不记录用户名、密码或法律数据。先查看确认信息，再显式执行：

```powershell
python scripts/create_pilot_backup.py
python scripts/create_pilot_backup.py --confirm --output-dir data/backups
```

每次执行会创建一个新的 `pilot-backup-<UTC 时间戳>/` 目录，包含数据库导出、`uploads/` 与 `chroma_db/` 归档、各文件 SHA-256 以及恢复要求。将该目录复制到独立的受控备份存储，不要把它提交到代码仓库或通过即时通信工具传递。

### 灾备目标（RTO / RPO）

- **RTO ≤ 30 分钟**：从故障到服务可用的最大可接受时间。恢复时间 = 备份导入时间 + 应用启动/健康检查。试点数据量小（实测 1.8MB dump、94 张表导入约 3.5 秒），远低于目标。
- **RPO ≤ 4 小时**：故障时最多可接受丢失最近 4 小时内的数据。这要求备份间隔 **≤ 4 小时**，与是否成功恢复无关。每日一次备份最多丢 24 小时数据，不满足该目标。

**满足 RPO 的备份节奏（必须由主机级定时任务驱动，不依赖应用容器）：**

```powershell
# Windows 计划任务：每 3 小时执行一次（保证 RPO≤4h），备份保留最近 7 份
# 一次性注册示例（替换工作目录）：
schtasks /Create /TN "aibg-pilot-backup" /SC HOURLY /MO 3 ^
  /TR "powershell -NoProfile -Command \"Set-Location 'D:\AI\llmXM\AI法律助手'; \$env:DATABASE_URL='mysql+pymysql://root:123456@localhost:3306/aibg'; python scripts/create_pilot_backup.py --confirm --output-dir data/backups\"" ^
  /ST 02:30
```

Linux 服务器则用 cron：`15 */3 * * * cd /srv/aibg && DATABASE_URL=... python scripts/create_pilot_backup.py --confirm --output-dir data/backups`。备份产物应在每小时检查中确认新增且校验 SHA-256 通过。容器内不安装 mysqldump，因此不要用 Celery Beat 调度本备份任务。

### 恢复演练记录

恢复演练必须使用隔离的 MySQL/PostgreSQL 实例和隔离的应用数据目录：核验 `manifest.json` 中 SHA-256 后导入数据库导出，解压 `application-data.tar.gz`，以隔离环境的加密密钥启动应用并执行 `/api/health/ready`、管理员登录、一个脱敏文档读取和审计日志查询。记录开始时间、恢复完成时间、备份时间和验证结果；演练失败时不要切换试点环境。

最近一次实测（2026-08-02，本机 MySQL 8.4）：

| 项 | 值 |
|---|---|
| 备份来源 | `aibg`（94 张表，dump 1.8MB） |
| 恢复目标 | 隔离库 `aibg_dr_restore` |
| 备份时间点（UTC） | 2026-08-02T08:39:11Z |
| 恢复导入耗时 | 3.49s |
| 完整性校验 | 94/94 表存在，行数全部一致 |
| 实测 RTO | ≈ 4s（导入 + 启动校验），达标（≤30min） |
| 实测 RPO | 取决于备份节奏；本次演练备份即为最新，RPO≈0。日常按上面 ≤3h 定时备份可稳定 ≤4h |

演练后恢复库已删除；备份目录保留在 `data/backups/pilot-backup-20260802T083911Z/`。

在发布试点版本前执行浏览器回归。默认用例使用受控 API Mock 验证真实前端页面的登录、合同审查、关键日期与门户创建请求，不依赖邮件、LLM、数据库或支付服务：

```powershell
Set-Location frontend
npm run test:e2e
```

首次在新机器上执行时，若 Playwright 提示缺少 Chromium，先运行一次 `npx playwright install chromium` 下载测试浏览器；该命令只写入本机 Playwright 缓存。

准备了独立测试账号、组织、案件以及 API 服务后，可额外运行真实环境验收。该用例会创建和读取测试数据，只能指向隔离的测试环境，不能指向试点用户数据：

```powershell
$env:E2E_RUN_INTEGRATION = 'true'
$env:E2E_USERNAME = 'test_lawyer'
$env:E2E_PASSWORD = '<测试密码>'
$env:E2E_ORG_ID = '<测试组织 ID>'
$env:E2E_CASE_ID = '<测试案件 ID>'
npm run test:e2e
```

## 加密密钥轮换（P0-07）

法律数据加密使用 AES-256-GCM，密钥以版本化密钥环 `LEGAL_DATA_ENCRYPTION_KEYS_JSON` 管理（`{"v1":"<base64 32字节>"}`），激活版本由 `LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION` 指定。轮换由 `scripts/rotate_encryption_key.py` 完成：

```powershell
# 1) 先备份（见上文）
python scripts/create_pilot_backup.py --confirm --output-dir data/backups

# 2) 只读检查当前各加密列的版本分布（明文/enc:vN）
python -B scripts/rotate_encryption_key.py --dry-run

# 3) 轮换到新版本：自动生成新密钥并把存量行重写为新版本
python -B scripts/rotate_encryption_key.py

#    或用指定密钥轮换：
#    python -B scripts/rotate_encryption_key.py --new-key <32字节URL-safe Base64>
```

脚本只打印需要写入 `.env` 的 `LEGAL_DATA_ENCRYPTION_KEYS_JSON` 与 `LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION`（`env_to_set`），**不修改 `.env`**。轮换后：

1. 把 `env_to_set` 两个值写入 `.env`。
2. 重启 API 与 Celery Worker（配置读取后才生效）。
3. 运行 `python -B scripts/rotate_encryption_key.py --verify`，确认全部行可解密、版本分布为新版本。
4. 确认存量行已全部为新版本后，从 `LEGAL_DATA_ENCRYPTION_KEYS_JSON` 摘除旧版本密钥，再次 `--verify` 通过即完成。

该脚本同样会**加密仍为明文的存量行**（惰性迁移遗留，如早期绕过 ORM 直写的数据），因此在首次配置密钥时运行一次 `--new-key` 即可一并收口明文存量。轮换演练（2026-08-02，隔离库）已验证：明文→v1→v2 两次轮换均可解密、旧密钥摘除后仍可读。

## 试点操作边界

- 为每个试点团队建立独立组织；敏感案件启用严格模式并仅添加必要成员。
- 法律结论、合同风险和文书初稿必须经过人工审核，不直接作为正式法律意见或自动外发内容。
- 客户门户仅使用短期链接与 OTP；未配置 SMTP 时不向客户发布门户链接。
- 禁止在 `.env`、日志、工单或截图中记录 LLM、数据库和加密密钥。

## 每日检查

1. 查看 `/api/health/ready`、Docker 服务状态和 Worker/Beat 日志。
2. 查看 Agent 失败运行、审核队列、门户 OTP 异常和资源访问审计。
3. 确认定时备份任务按 ≤3h 间隔正常产出（RPO≤4h），核对新备份 SHA-256 并转移到受控存储；按计划在隔离环境完成恢复演练。
4. 若发现越权、错误引用或异常外发，立即撤销相关门户链接、停用账户并保留审计记录。
