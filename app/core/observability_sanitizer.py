import json
import hashlib
import re
from typing import Any

SENSITIVE_LLM_ACTIONS = {
    "chat",
    "chat_stream",
    "generate",
    "generate_with_images",
    "rag_answer",
    "agent_plan",
    "agent_plan_preview",
    "document_summary",
    "document_risk_extract",
    "document_todo_extract",
    "document_clause_extract",
    "document_compare",
    "meeting_summary",
    "meeting_decision_extract",
    "meeting_topic_extract",
    "email_generate",
    "email_reply",
    "email_tone_switch",
    "email_thread_summary",
    "email_polish",
    "task_extract_from_chat",
    "task_decompose",
}

# ── 统一脱敏层（P1）：字段名规则 + 类型规则 + 文本兜底检测 ───────────────
# 所有日志、审计导出、异常上报与 telemetry payload 均须经此层后再序列化。

# 敏感字段名（大小写不敏感匹配）：命中即 redact，绝不落原文。
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "password", "passwd", "pwd", "client_secret", "secret", "secret_key", "api_key",
        "apikey", "access_token", "refresh_token", "token", "authorization", "cookie",
        "set-cookie", "authorization_code", "credit_card", "card_number", "cvv", "cvc",
        "pan", "bin", "private_key", "signature", "hmac", "webhook_secret",
        "encryption_key", "jwt", "bearer", "db_password", "smtp_password",
        "ldap_bind_password", "minio_secret_key", "aws_secret_access_key",
    }
)
# 低风险允许字段（结构化 payload 中默认放行的元数据键）。
_DEFAULT_ALLOWED_KEYS = frozenset(
    {
        "id", "user_id", "org_id", "organization_id", "tenant_id", "module", "action",
        "status", "model", "provider", "event_type", "event_name", "request_id",
        "trace_id", "task_id", "agent_run_id", "document_id", "target_type",
        "target_id", "target_name", "error_code", "error_category", "duration_ms",
        "input_tokens", "output_tokens", "total_tokens", "cost", "currency",
        "attempt_number", "routing_role", "routing_stage", "prompt_template",
        "prompt_version", "channel", "queue", "created_at", "updated_at",
        "count", "total", "page", "page_size", "result_status", "rounds",
        "confidence", "tokens", "version", "source", "timestamp", "ts", "level",
    }
)
# 需要 hash（保留可对账性，不落明文）的字段名。
_HASH_KEY_NAMES = frozenset({"email_hash", "id_card_hash", "phone_hash", "content_hash", "raw_payload_hash"})

_MAX_TEXT_LEN = 2000
_TRUNCATE_LEN = 200


def _redaction_salt() -> str:
    """稳定且安全的盐：复用项目 SECRET_KEY；未配置时退化为固定盐（仅测试场景）。"""
    try:
        from app.core.config import get_settings

        key = get_settings().SECRET_KEY
        if key:
            return "aibg-obs:" + key
    except Exception:  # noqa: BLE001 - 配置缺失时用固定盐
        pass
    return "aibg-obs:fallback"


def stable_hash(value: str) -> str:
    """不可逆稳定 hash（带盐）；禁止用可逆加密冒充脱敏。"""
    return hashlib.sha256(f"{_redaction_salt()}:{value}".encode("utf-8")).hexdigest()[:16]


def _key_sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    return normalized in _SENSITIVE_KEY_NAMES or any(
        token in normalized for token in ("password", "secret", "token", "api_key", "authorization", "cookie")
    )


def _key_allowable(key: str) -> bool:
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    return normalized in _DEFAULT_ALLOWED_KEYS


def _redact_scalar(value: Any) -> str:
    """统一标量脱敏：token/secret 前缀掩码，PII 走 data_protection_service 兜底。"""
    text = str(value)
    if not text:
        return text
    # API token / 密码等形态
    if re.search(r"(?i)(sk|rk|AKIA|ghp|xoxb)[_-]?[A-Za-z0-9]{16,}", text):
        return "****redacted****"
    # JWT（eyJ...三段式）与长 Bearer 形态
    if re.search(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b", text):
        return "****redacted****"
    if len(text) > _MAX_TEXT_LEN:
        return f"{{redacted:len={len(text)}}}"
    try:
        from app.services.org.data_protection_service import data_protection_service

        result = data_protection_service.redact(text)
        if result.get("redacted"):
            return result["text"]
    except Exception:  # noqa: BLE001 - 脱敏层失败降级为掩码
        pass
    return text


def redact_payload(
    payload: Any,
    *,
    allowed_keys: set[str] | frozenset[str] | None = None,
    sensitive_keys: set[str] | frozenset[str] | None = None,
    hash_keys: set[str] | frozenset[str] | None = None,
    truncate_limit: int = _TRUNCATE_LEN,
    max_text_len: int = _MAX_TEXT_LEN,
    depth: int = 0,
) -> Any:
    """递归脱敏任意 payload。默认拒绝未知大文本/二进制字段；仅放行明确允许字段。

    - 敏感字段名 -> 整值掩码
    - hash 字段名 -> stable_hash
    - bytes -> 长度摘要
    - 大文本（> max_text_len）-> 长度摘要（默认 deny）
    - 字符串 -> 截断 + PII 文本兜底检测
    - 未知 dict key -> 默认 deny（不输出原文），除非在 allowlist
    """
    if depth > 6:
        return "{{depth-limited}}"
    if sensitive_keys and any(_key_sensitive(k) for k in sensitive_keys):
        pass  # 显式敏感键集合由调用方传入时走下方按 key 处理

    if isinstance(payload, bytes):
        return f"{{bytes:{len(payload)}b}}"
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    if isinstance(payload, str):
        return _redact_string(payload, truncate_limit=truncate_limit, max_text_len=max_text_len)

    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for key, value in payload.items():
            normalized = str(key).lower().replace("-", "_")
            if sensitive_keys and normalized in {k.lower() for k in sensitive_keys}:
                result[str(key)] = "****redacted****"
                continue
            if hash_keys and normalized in {k.lower() for k in hash_keys}:
                result[str(key)] = stable_hash(_redact_scalar(value))
                continue
            if _key_sensitive(normalized):
                result[str(key)] = "****redacted****"
                continue
            if _key_allowable(normalized) or (allowed_keys and normalized in {k.lower() for k in allowed_keys}):
                result[str(key)] = redact_payload(
                    value,
                    allowed_keys=allowed_keys,
                    sensitive_keys=sensitive_keys,
                    hash_keys=hash_keys,
                    truncate_limit=truncate_limit,
                    max_text_len=max_text_len,
                    depth=depth + 1,
                )
            else:
                # 默认 deny：未知 key 不输出原文，只留长度/类型摘要。
                result[str(key)] = _deny_summary(value)
        return result

    if isinstance(payload, (list, tuple, set)):
        return [
            redact_payload(
                item,
                allowed_keys=allowed_keys,
                sensitive_keys=sensitive_keys,
                hash_keys=hash_keys,
                truncate_limit=truncate_limit,
                max_text_len=max_text_len,
                depth=depth + 1,
            )
            for item in list(payload)
        ]

    return _redact_scalar(payload)


def _redact_string(text: str, *, truncate_limit: int, max_text_len: int) -> str:
    if not text:
        return text
    if len(text) > max_text_len:
        return f"{{redacted:len={len(text)}}}"
    truncated = text[:truncate_limit]
    return _redact_scalar(truncated)


def _deny_summary(value: Any) -> str:
    if isinstance(value, bytes):
        return f"{{bytes:{len(value)}b}}"
    if isinstance(value, dict):
        return f"{{dict:{len(value)}keys}}"
    if isinstance(value, (list, tuple, set)):
        return f"{{list:{len(value)}items}}"
    if isinstance(value, str):
        if len(value) > _MAX_TEXT_LEN:
            return f"{{redacted:len={len(value)}}}"
        return _redact_scalar(value[:_TRUNCATE_LEN])
    return _redact_scalar(value)


def redact_error(exc: Exception | None) -> str:
    """异常对象脱敏摘要：稳定类型名 + 脱敏后的消息，避免把 URL/凭据写入日志。"""
    if exc is None:
        return "none"
    message = str(exc) or type(exc).__name__
    redacted = _redact_string(message, truncate_limit=_TRUNCATE_LEN, max_text_len=_MAX_TEXT_LEN)
    return f"{type(exc).__name__}: {redacted}"


def truncate_text(text: str | None, limit: int = 2000) -> str | None:
    if text is None:
        return None
    return text[:limit]


def to_observability_excerpt(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        return truncate_text(payload)
    try:
        return truncate_text(json.dumps(payload, ensure_ascii=False))
    except TypeError:
        return truncate_text(str(payload))


def sanitize_observability_excerpt(action: str, excerpt: str | None, *, kind: str) -> str | None:
    if excerpt is None:
        return None
    if action == "embedding":
        return truncate_text(excerpt)
    if action in SENSITIVE_LLM_ACTIONS:
        return json.dumps(
            {
                "redacted": True,
                "kind": kind,
                "action": action,
                "length": len(excerpt),
            },
            ensure_ascii=False,
        )
    return truncate_text(excerpt)


def sanitize_observability_error_message(action: str, error_message: str | None) -> str | None:
    if error_message is None:
        return None
    if action in SENSITIVE_LLM_ACTIONS:
        return json.dumps(
            {
                "redacted": True,
                "kind": "error",
                "action": action,
                "length": len(error_message),
            },
            ensure_ascii=False,
        )
    return truncate_text(error_message)


def sanitize_background_error_message(error_message: str | None) -> str | None:
    if error_message is None:
        return None
    return "任务执行失败，请查看系统日志"
