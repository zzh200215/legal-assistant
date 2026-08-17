"""Webhook 安全审计（P1-C）。

- 复用 security_audit_service.write_event（追加式哈希链），事件类型 ``webhook``。
- 只记录 provider / 错误码 / 事件类型等元数据，**绝不包含签名密钥或完整敏感载荷**。
- 审计写失败按 security_audit_service 的降级策略处理（不阻断验签拒绝本身）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def write_webhook_audit(
    *,
    db: Session,
    result: str,
    provider: str,
    reason_code: str | None = None,
    sanitized_metadata: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    organization_id: int | None = None,
) -> None:
    """记录一次 Webhook 验证结果（通常为 blocked）。"""
    from app.services.org.security_audit_service import write_event

    write_event(
        event_type="webhook",
        actor_type="system",
        actor_id="webhook_system",
        result=result,
        organization_id=organization_id,
        target_type=target_type,
        target_id=target_id,
        action="webhook_verification",
        reason_code=reason_code,
        sanitized_metadata=sanitized_metadata,
        db=db,
    )