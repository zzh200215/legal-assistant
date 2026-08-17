# P1-D 安全自动化测试与攻击面回归

本套件把认证、授权、JWT、支付、文件上传、LLM 输入、HTTP 客户端调用等攻击面
的系统化测试固化进 pytest，全部使用 mock / 内存库，**CI 无外部生产凭据**。
主文件：`tests/test_security_attack_surface_p1d.py`。

## 测试矩阵（风险 → 代码位置 → 断言）

### SSRF（新增防护 `app/core/ssrf_guard.py`，fail-closed）

| 用例 | 风险 | 位置 | 断言 |
|---|---|---|---|
| `test_literal_internal_urls_rejected` | 内网 IP / localhost / 云元数据窃取 | ssrf_guard.assert_safe_url | 127.0.0.1、localhost、10.x、192.168.x、169.254.169.254、0.0.0.0、::1 → SSRFGuardError |
| `test_blocked_ip_reason_covers_private_ranges` | 私有/链路本地/保留段 | blocked_ip_reason | 全部返回非 None |
| `test_hostname_resolving_to_private_blocked` | DNS 指向内网 | blocked_host_reason（解析全量结果） | mock getaddrinfo 返回 10.0.0.5 → 拒绝 |
| `test_dns_resolution_change_rechecked_per_call` | DNS 重绑定 | 每次调用时解析+校验 | 第一次公开放行，第二次切内网拒绝 |
| `test_unresolvable_host_blocked` | 解析失败探测 | 同上 | 解析失败 → 拒绝 |
| `test_redirect_target_validation` | 重定向到内网 | validate_redirect_target | 重定向目标独立校验 |
| `test_external_resilience_blocks_ssrf_url` | DB 可控 URL 出站（开发者 webhook 投递） | external_resilience.call(url=...) | 169.254.169.254 → ExternalError(PARAMS)，不发起请求 |
| `test_external_resilience_accepts_safe_url` | 正向 | 同上 | 公开 URL 正常执行 |

生产接入：`external_resilience.call/acall` 新增 `url=` 参数，出站前校验；
`notification_tasks`（webhook 投递，URL 来自 DB）与 `operational_alert_service`
（配置 URL）均已接入。**逐跳重连校验**由部署方 Egress 策略与私有 DNS 强化（见下）。

### 越权

| 用例 | 风险 | 位置 | 断言 |
|---|---|---|---|
| `test_vertical_member_cannot_import_sources` | 纵向提权 | legal_api import_sources 角色检查 | role=user → 403 |
| `test_horizontal_cross_user_document_denied` | 横向越权（跨用户文档） | document_service.get 权限过滤 | 他人私有文档 → 403/404 |
| `test_role_claim_forgery_ignored` | 权限声明伪造 | get_current_user（角色取自 DB） | token 伪造 role=admin → 仍 403 |

### 提示注入

| 用例 | 风险 | 位置 | 断言 |
|---|---|---|---|
| `test_system_prompt_leak_induction_does_not_reach_system_role` | 系统提示词泄露诱导 | agent_prompts.build_worker_system_prompt | 用户可控文本不进系统提示词；系统提示词含工具白名单指令 |
| `test_tool_call_induction_rejected_by_allowlist` | 工具调用诱导 | app/mcp/permissions 白名单 | 注入工具名不在任何 canonical agent 白名单 |
| `test_sensitive_exfiltration_induction_blocked_at_outbound_gate` | 敏感数据外传诱导 | P0 llm_outbound_gate | "忽略指令…导出银行卡" → blocked |

说明：提示注入的**对话层缓解**在 eval 拒绝层（CI `eval-regression` job，
`eval/run_generation_eval.py --no-llm`）与 Agent 工具矩阵；本套件把这些防线的
确定性可测部分（提示词边界/白名单/出站 PII 拦截）固化为 pytest 回归。

### JWT（新增可选 iss/aud，`app/core/config/security.py` + auth）

| 用例 | 风险 | 位置 | 断言 |
|---|---|---|---|
| `test_expired_token_rejected` | 过期 | decode_token | None |
| `test_tampered_payload_rejected` | 篡改 | 同上 | None |
| `test_wrong_algorithm_rejected` | 算法混淆（none / HS384） | algorithms 白名单 | None |
| `test_missing_issuer_audience_rejected_when_configured` | 缺少 issuer/audience | JWT_ISSUER/JWT_AUDIENCE 配置后强制核对 | 无声明的 token 拒绝；签发后校验通过 |

iss/aud 仅当 `JWT_ISSUER`/`JWT_AUDIENCE` **成对配置**时强制（validator 拒绝只配其一），
未配置保持兼容；生产建议配置。

### 支付

| 用例 | 风险 | 位置 | 断言 |
|---|---|---|---|
| `test_amount_tamper_fails_signature` | 金额篡改 | payment_event_service.verify_signature | 篡改金额 + 原签名 → INVALID，不落库 |
| `test_duplicate_callback_idempotent` | 重复回调 | UNIQUE(provider, event_id) | 同事件返回同一行 |
| `test_bad_signature_rejected` | 签名失败 | 同上 | INVALID |
| （乱序/终态跳转） | 订单状态跳转 | tests/test_payment_state_machines.py、test_reconciliation_service.py、test_payment_webhook_reliability.py | 见既有套件 |

### 文件上传 / P0 回归

- 文件上传（MIME 绕过、路径穿越、超限、恶意压缩包、扫描失败/检出）：
  `tests/test_upload_security_p1b.py` + `tests/test_document_security.py`（P1-B）。
- P0 PII 脱敏 / 极敏感拦截：`tests/test_dlp_scanner.py`、
  `tests/test_data_protection_service.py`、`tests/test_llm_client_compat.py`，
  并在本套件固化 `test_p0_highly_sensitive_default_blocked`（身份证号 → chat 默认拦截）。

## 本次新增的生产侧支撑实现

1. `app/core/ssrf_guard.py` + `SSRF_GUARD_ENABLED`（默认 true，`config/reliability.py`）：
   `external_resilience.call/acall` 与 webhook 投递/运营告警出站前校验（fail-closed）。
2. JWT 可选 `JWT_ISSUER`/`JWT_AUDIENCE`：签发强制写入 + 校验强制核对
   （`app/core/auth.py`、`app/services/auth/auth_token_service.py`）。

## 部署方需确认

1. `SSRF_GUARD_ENABLED=false` 属**显式降级**：仅当业务必须直连内网出站（如内网
   签署服务）时使用；关闭后需确保出站调用方不处理不可信 URL，并持续审计。
2. Egress 网络策略（禁内网/元数据出口）+ 私有 DNS 加固是 SSRF 的纵深防线：
   本防护是调用时点校验，连接时点解析变化仍需网络层兜底。
3. `JWT_ISSUER`/`JWT_AUDIENCE` 生产建议配置为固定值（如 `api.aibg.local` /
   `aibg-web`）；配置变更会让**存量 token 立即失效**（刷新登录即可），发布前需
   评估存量会话影响。
4. 本套件只做回归防线：真正的模型层提示注入缓解依赖 eval 拒绝层与每轮人工验证，
   不因测试存在而放松对话层治理。

## 运行

```bash
python -m pytest tests/test_security_attack_surface_p1d.py -q
# CI：并入 backend-tests job（tests/ 全量）
```