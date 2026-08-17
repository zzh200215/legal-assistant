"""上传安全审计：上传被拒时写入 security_audit_events（不含文件内容）。

- 复用 security_audit_service.write_event（追加式哈希链），事件类型 document_upload。
- 只记录元数据（文件名/扩展名/大小/错误码/原因），绝不记录文件内容/哈希内容。
- 审计写失败按 security_audit_service 的降级策略处理（不阻断上传拒绝本身）。
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session


def write_upload_rejected_audit(
    *,
    db: Session,
    organization_id: int | None,
    actor_id: str | int | None,
    filename: str,
    ext: str,
    size_bytes: int | None,
    error_code: str,
    reason: str | None = None,
    target_type: str = "document",
) -> None:
    """记录一次上传安全拒绝（伪造 MIME / 超限 / zip-bomb / 病毒 / 类型不允许）。

    元数据仅含文件元信息与拒绝码；文件名可能含个人信息但属于审计元数据，
    不包含文件内容本身。
    """
    from app.services.org.security_audit_service import write_event

    metadata: dict = {
        "filename": str(filename)[:255] or "",
        "ext": (ext or "").lower(),
        "size_bytes": int(size_bytes or 0),
        "error_code": error_code,
    }
    if reason:
        metadata["reason"] = str(reason)[:500]

    write_event(
        event_type="document_upload",
        actor_type="user",
        result="blocked",
        organization_id=organization_id,
        actor_id=str(actor_id) if actor_id is not None else None,
        target_type=target_type,
        target_id=None,
        action="upload_rejected",
        reason_code=error_code,
        sanitized_metadata=json.dumps(metadata, ensure_ascii=False),
        db=db,
    )
