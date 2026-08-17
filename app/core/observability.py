from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from app.core import database as database_module
from app.core.config import get_settings

_audit_handler_configured = False


# 日志类型：access / business / security / audit / model
LOG_TYPE_ACCESS = "access"
LOG_TYPE_BUSINESS = "business"
LOG_TYPE_SECURITY = "security"
LOG_TYPE_AUDIT = "audit"
LOG_TYPE_MODEL = "model"

# 稳定错误类别（供日志/指标/审计统一口径，不用自由文本异常作唯一检索字段）。
ERROR_CATEGORY_TIMEOUT = "timeout"
ERROR_CATEGORY_NETWORK = "network"
ERROR_CATEGORY_MODEL = "model"
ERROR_CATEGORY_PERMISSION = "permission"
ERROR_CATEGORY_DATA = "data"
ERROR_CATEGORY_AGENT = "agent"
ERROR_CATEGORY_BUSINESS = "business"
ERROR_CATEGORY_SYSTEM = "system"

_CATEGORY_MARKERS: list[tuple[tuple[str, ...], str]] = [
    (("timeout", "timed out", "超时"), ERROR_CATEGORY_TIMEOUT),
    (("network", "connection", "dns", "socket", "connect", "网络", "连接"), ERROR_CATEGORY_NETWORK),
    (("model", "llm", "token", "context_length", "rate limit", "模型"), ERROR_CATEGORY_MODEL),
    (("permission", "forbidden", "unauthorized", "403", "无权", "权限", "未授权"), ERROR_CATEGORY_PERMISSION),
    (("agent", "tool", "工具", "approval"), ERROR_CATEGORY_AGENT),
    (("not found", "missing", "validation", "json", "parse", "schema", "不存在", "数据"), ERROR_CATEGORY_DATA),
]


def classify_error_category(error: Exception | str | None) -> str:
    """按稳定错误码/异常类型/文本关键词归一化错误类别；未知归 system。"""
    if error is None:
        return ERROR_CATEGORY_SYSTEM
    text = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
    low = text.lower()
    for markers, category in _CATEGORY_MARKERS:
        if any(marker in low for marker in markers):
            return category
    if isinstance(error, Exception):
        name = type(error).__name__
        if name in ("TimeoutError", "SoftTimeLimitExceeded", "ConnectTimeout", "ReadTimeout"):
            return ERROR_CATEGORY_TIMEOUT
        if name in ("PermissionDenied", "Forbidden", "Unauthorized", "ApiKeyInvalid"):
            return ERROR_CATEGORY_PERMISSION
        if name in ("ModelError", "ModelUnavailable", "LLMError"):
            return ERROR_CATEGORY_MODEL
    return ERROR_CATEGORY_SYSTEM


def _ensure_audit_file_handler() -> None:
    """把 audit.json logger 接到落盘 FileHandler（幂等，供 SIEM/集中日志采集）。

    仅在 STRUCTURED_LOG_JSON_LINES 开启时由 structured_log_json 调用一次；
    目录不存在自动创建；重复调用不重复添加 handler。
    """
    global _audit_handler_configured
    if _audit_handler_configured:
        return
    logger = logging.getLogger("audit.json")
    if not logger.handlers:
        try:
            path = get_settings().STRUCTURED_LOG_FILE.strip()
            if path:
                directory = os.path.dirname(os.path.abspath(path))
                if directory:
                    os.makedirs(directory, exist_ok=True)
                handler = logging.FileHandler(path, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
                logger.setLevel(logging.INFO)
                logger.propagate = False
        except OSError as exc:
            logging.getLogger(__name__).warning("audit.json 落盘失败，结构化日志仅输出到 root: %s", exc)
    _audit_handler_configured = True


def structured_log_json(
    *,
    source: str,
    action: str,
    module: Optional[str] = None,
    actor: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    target_name: Optional[str] = None,
    detail: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """等保差距 #2：SIEM 汇聚用结构化 JSON 行（旧签名兼容）。

    STRUCTURED_LOG_JSON_LINES 开启时，把双轨日志（operation/audit/login）以单行
    JSON 输出到 audit.json 日志（自动落盘 STRUCTURED_LOG_FILE，供集中日志/ELK 采集）；
    关闭时零开销。P1 起底层委托 structured_observe（追加上下文字段，保持字段兼容）。
    """
    log_type = {
        "audit_log": LOG_TYPE_AUDIT,
        "login_log": LOG_TYPE_SECURITY,
        "operation_log": LOG_TYPE_BUSINESS,
        "notification": LOG_TYPE_BUSINESS,
        "security": LOG_TYPE_SECURITY,
    }.get(source, LOG_TYPE_BUSINESS)
    structured_observe(
        source=source,
        log_type=log_type,
        event_name=action,
        module=module,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        detail=detail,
        ip_address=ip_address,
    )


def structured_observe(
    *,
    log_type: str,
    event_name: str,
    level: str = "info",
    source: Optional[str] = None,
    module: Optional[str] = None,
    actor: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    target_name: Optional[str] = None,
    detail: Optional[str] = None,
    ip_address: Optional[str] = None,
    duration_ms: Optional[int] = None,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
    error_category: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """统一结构化日志（P1）：按日志类型输出，序列化前统一脱敏。

    - access/business/security/audit/model 五类；事件名/错误码稳定。
    - 自动附加 request_id/trace_id/user_id/org_id/task_id/agent_run_id 与
      service/environment（缺失用 unknown，不伪造关联）。
    - detail/extra 先经 redact_payload 脱敏再序列化，绝不落原文。
    - STRUCTURED_LOG_JSON_LINES 关闭时零开销。
    """
    if not get_settings().STRUCTURED_LOG_JSON_LINES:
        return
    from app.core.obs_context import get_context
    from app.core.observability_sanitizer import redact_payload

    ctx = get_context()
    payload: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "log_type": log_type,
        "event_name": event_name,
        "action": event_name,  # 兼容旧 SIEM 字段
        "level": level,
        "source": source,
        "module": module,
        "actor": actor,
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target_name,
        "ip_address": ip_address,
        "duration_ms": duration_ms,
        "outcome": outcome,
        "error_code": error_code,
        "error_category": error_category,
    }
    payload.update(ctx.to_dict())
    if detail is not None:
        payload["detail"] = redact_payload(detail)
    if extra:
        payload["extra"] = redact_payload(extra)
    _ensure_audit_file_handler()
    logging.getLogger("audit.json").info(json.dumps(payload, ensure_ascii=False, default=str))


def log_access(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_ms: int,
    client_ip: Optional[str] = None,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
) -> None:
    """access 日志：仅记录方法/路由模板/状态码/耗时/客户端 IP 摘要。

    禁止记录完整 query/body、Cookie、Authorization、token。
    OBS_ACCESS_LOG_ENABLED 关闭时零开销。
    """
    if not get_settings().OBS_ACCESS_LOG_ENABLED:
        return
    outcome = outcome or ("ok" if status_code < 400 else ("error" if status_code >= 500 else "client_error"))
    structured_observe(
        log_type=LOG_TYPE_ACCESS,
        event_name="http_request",
        module="api",
        target_type="route",
        target_name=route,
        ip_address=client_ip,
        duration_ms=duration_ms,
        outcome=outcome,
        error_code=error_code,
        extra={"method": method, "status_code": status_code},
    )


def log_business_event(
    *,
    event_name: str,
    module: str,
    detail: Optional[str] = None,
    actor: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    target_name: Optional[str] = None,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
    duration_ms: Optional[int] = None,
    extra: Optional[dict] = None,
) -> None:
    structured_observe(
        log_type=LOG_TYPE_BUSINESS,
        event_name=event_name,
        module=module,
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        detail=detail,
        outcome=outcome,
        error_code=error_code,
        duration_ms=duration_ms,
        extra=extra,
    )


def log_security_event(
    *,
    event_name: str,
    detail: Optional[str] = None,
    actor: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    ip_address: Optional[str] = None,
    outcome: Optional[str] = None,
    error_code: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    structured_observe(
        log_type=LOG_TYPE_SECURITY,
        event_name=event_name,
        module="security",
        actor=actor,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip_address=ip_address,
        outcome=outcome,
        error_code=error_code,
        extra=extra,
    )


def log_model_event(
    *,
    event_name: str,
    module: str,
    model_name: str,
    provider: Optional[str] = None,
    status: str = "success",
    duration_ms: Optional[int] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    error_code: Optional[str] = None,
    error_category: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """model 类型结构化日志（P1）：只记稳定摘要/用量/状态，绝不记录 prompt/正文/回复原文。

    OBS_MODEL_DETAIL_LOG_ENABLED 关闭（默认）时零开销。
    """
    if not get_settings().OBS_MODEL_DETAIL_LOG_ENABLED:
        return
    payload_extra: dict = {
        "model": model_name,
        "status": status,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if provider:
        payload_extra["provider"] = provider
    if extra:
        payload_extra.update(extra)
    structured_observe(
        log_type=LOG_TYPE_MODEL,
        event_name=event_name,
        module=module,
        target_type="model",
        target_name=model_name,
        outcome=status,
        duration_ms=duration_ms,
        error_code=error_code,
        error_category=error_category,
        extra=payload_extra,
    )


def log_async_task_event(
    *,
    user_id: int | None,
    module: str,
    action: str,
    target_type: str,
    target_id: int | None,
    detail: str,
) -> None:
    db = database_module.SessionLocal()
    try:
        from app.services.observability.oplog_service import oplog_service

        oplog_service.log(
            module=module,
            action=action,
            db=db,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    finally:
        db.close()
