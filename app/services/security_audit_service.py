"""不可篡改安全审计事件服务

哈希链机制：
  current_hash = sha256(seq_no | event_type | actor_id | occurred_at_iso | prev_hash)
  seq_no 通过 Redis INCR 原子递增，保证全局唯一且有序。
  若 Redis 不可用，降级为 DB max(seq_no)+1（仍需外部串行化保证）。
"""

import hashlib
from datetime import datetime, timezone
from typing import Optional

import redis as redis_lib

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.legal_notifications import SecurityAuditEvent

_SEQ_KEY = "security_audit:seq_no"


def _next_seq_no() -> int:
    try:
        r = redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)
        return int(r.incr(_SEQ_KEY))
    except Exception:
        db = SessionLocal()
        try:
            row = db.query(SecurityAuditEvent).order_by(
                SecurityAuditEvent.seq_no.desc()
            ).first()
            return (row.seq_no + 1) if row else 1
        finally:
            db.close()


def _compute_hash(
    seq_no: int,
    event_type: str,
    actor_id: str,
    occurred_at: datetime,
    prev_hash: Optional[str],
) -> str:
    raw = f"{seq_no}|{event_type}|{actor_id}|{occurred_at.isoformat()}|{prev_hash or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def write_event(
    *,
    event_type: str,
    actor_type: str,
    result: str,
    organization_id: Optional[int] = None,
    actor_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    detail_json_hash: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    db=None,
) -> Optional[SecurityAuditEvent]:
    """写入一条安全审计事件，返回写入的事件对象；失败时仅记录日志，不抛异常。

    参数:
        event_type: 事件类型白名单之一（login/permission_change/export/portal_access/key_op/sign_callback/admin_view）
        actor_type: user / system / portal_visitor / api_key
        result:     success / failure / blocked
        actor_id:   已脱敏的主体标识（user_id 字符串 / api_key 前缀）
        detail_json_hash: 详情摘要哈希，敏感数据不入库
    """
    _own_db = db is None
    if _own_db:
        db = SessionLocal()
    try:
        now = occurred_at or datetime.now(timezone.utc)
        seq_no = _next_seq_no()

        prev = db.query(SecurityAuditEvent).order_by(
            SecurityAuditEvent.seq_no.desc()
        ).first()
        prev_hash = prev.current_hash if prev else None

        current_hash = _compute_hash(
            seq_no=seq_no,
            event_type=event_type,
            actor_id=actor_id or "",
            occurred_at=now,
            prev_hash=prev_hash,
        )

        event = SecurityAuditEvent(
            organization_id=organization_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            result=result,
            detail_json_hash=detail_json_hash,
            occurred_at=now,
            seq_no=seq_no,
            prev_hash=prev_hash,
            current_hash=current_hash,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None
    finally:
        if _own_db:
            db.close()


def verify_chain(organization_id: Optional[int] = None) -> dict:
    """校验哈希链完整性，返回断链位置（空列表=完整）。"""
    db = SessionLocal()
    try:
        q = db.query(SecurityAuditEvent).order_by(SecurityAuditEvent.seq_no)
        if organization_id is not None:
            q = q.filter(SecurityAuditEvent.organization_id == organization_id)
        events = q.all()

        broken = []
        for i, ev in enumerate(events):
            expected = _compute_hash(
                seq_no=ev.seq_no,
                event_type=ev.event_type,
                actor_id=ev.actor_id or "",
                occurred_at=ev.occurred_at,
                prev_hash=events[i - 1].current_hash if i > 0 else None,
            )
            if expected != ev.current_hash:
                broken.append({"seq_no": ev.seq_no, "id": ev.id})

        return {"total": len(events), "broken": broken, "intact": len(broken) == 0}
    finally:
        db.close()


security_audit_service = type("_Svc", (), {
    "write_event": staticmethod(write_event),
    "verify_chain": staticmethod(verify_chain),
})()
