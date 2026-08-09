"""统一业务错误码 — PRD V3.0 § 9.8.2

用法：
    from app.core.error_codes import err
    raise HTTPException(409, detail=err(INVOICE_IMMUTABLE))
    raise HTTPException(403, detail=err(LEGAL_RESOURCE_SCOPE_DENIED, "该资源不属于当前组织"))
"""

from typing import Optional

# ── 计时计费 ──────────────────────────────────────────────────────────────────
TIME_ENTRY_ALREADY_RUNNING    = "TIME_ENTRY_ALREADY_RUNNING"
INVOICE_IMMUTABLE             = "INVOICE_IMMUTABLE"
LEGAL_CASE_ARCHIVED           = "LEGAL_CASE_ARCHIVED"

# ── 门户 ──────────────────────────────────────────────────────────────────────
PORTAL_LINK_UNAVAILABLE       = "PORTAL_LINK_UNAVAILABLE"
PORTAL_OTP_INVALID            = "PORTAL_OTP_INVALID"
PORTAL_OTP_LOCKED             = "PORTAL_OTP_LOCKED"

# ── 合同 ──────────────────────────────────────────────────────────────────────
CONTRACT_VERSION_LOCKED       = "CONTRACT_VERSION_LOCKED"
SIGN_PROVIDER_UNAVAILABLE     = "SIGN_PROVIDER_UNAVAILABLE"

# ── 权限与范围 ────────────────────────────────────────────────────────────────
LEGAL_RESOURCE_SCOPE_DENIED   = "LEGAL_RESOURCE_SCOPE_DENIED"

# ── Open API ──────────────────────────────────────────────────────────────────
API_KEY_INVALID               = "API_KEY_INVALID"
API_KEY_IP_DENIED             = "API_KEY_IP_DENIED"
API_RATE_LIMIT_EXCEEDED       = "API_RATE_LIMIT_EXCEEDED"

# ── Webhook ───────────────────────────────────────────────────────────────────
WEBHOOK_SIGNATURE_INVALID     = "WEBHOOK_SIGNATURE_INVALID"

# ── 导出 ──────────────────────────────────────────────────────────────────────
EXPORT_ASYNC_REQUIRED         = "EXPORT_ASYNC_REQUIRED"

# ── 幂等 ──────────────────────────────────────────────────────────────────────
IDEMPOTENCY_KEY_CONFLICT      = "IDEMPOTENCY_KEY_CONFLICT"
IDEMPOTENCY_KEY_IN_PROGRESS   = "IDEMPOTENCY_KEY_IN_PROGRESS"

# ── 通用 ──────────────────────────────────────────────────────────────────────
VALIDATION_ERROR              = "VALIDATION_ERROR"
UNAUTHORIZED                  = "UNAUTHORIZED"
NOT_FOUND                     = "NOT_FOUND"


_DEFAULT_MESSAGES: dict[str, str] = {
    TIME_ENTRY_ALREADY_RUNNING:  "您已有一条运行中的计时，请先结束或暂停后再开始新计时",
    INVOICE_IMMUTABLE:           "已发送/已付款/已作废的账单不可修改，如需更正请作废后重新开票",
    LEGAL_CASE_ARCHIVED:         "案件已归档，不可新建计时、账单、日期或门户，如需操作请先恢复案件",
    PORTAL_LINK_UNAVAILABLE:     "链接不可用",
    PORTAL_OTP_INVALID:          "验证码错误，请重试",
    PORTAL_OTP_LOCKED:           "验证码连续错误次数过多，请15分钟后再试",
    CONTRACT_VERSION_LOCKED:     "该合同版本已签署或正在签署中，不可覆盖或删除，请创建后续版本",
    SIGN_PROVIDER_UNAVAILABLE:   "电子签名服务暂不可用，已保留草稿，请稍后重试",
    LEGAL_RESOURCE_SCOPE_DENIED: "资源不在当前组织、案件或成员权限范围内",
    API_KEY_INVALID:             "API Key 无效、已撤销或已过期",
    API_KEY_IP_DENIED:           "请求 IP 不在该 API Key 的白名单范围内，请联系应用管理员",
    API_RATE_LIMIT_EXCEEDED:     "调用次数超出当前套餐限制",
    WEBHOOK_SIGNATURE_INVALID:   "Webhook 签名校验失败",
    EXPORT_ASYNC_REQUIRED:       "导出数据量超出同步阈值，已创建异步任务，请通过任务 ID 查询结果",
    IDEMPOTENCY_KEY_CONFLICT:    "相同幂等键已存在但请求体不一致",
    IDEMPOTENCY_KEY_IN_PROGRESS: "该幂等键请求正在处理中，请稍后重试",
    VALIDATION_ERROR:            "参数校验失败",
    UNAUTHORIZED:                "未登录或登录已失效",
    NOT_FOUND:                   "资源不存在或无权访问",
}


def err(code: str, message: Optional[str] = None) -> dict:
    """生成标准错误 detail 字典，供 HTTPException(status_code, detail=err(...)) 使用。"""
    return {"code": code, "message": message or _DEFAULT_MESSAGES.get(code, code)}
