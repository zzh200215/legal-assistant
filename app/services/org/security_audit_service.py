"""不可篡改安全审计事件服务（P1 通用审计扩展）

哈希链机制（版本化公式，存量行零迁移成本）：
  schema_version=1（存量行）：current_hash = sha256(seq_no | event_type | actor_id |
      occurred_at_iso | prev_hash)
  schema_version=2（新行）：current_hash = sha256("v2" | seq_no | event_type | actor_id |
      occurred_at_iso | prev_hash | action | resource_version | request_id | trace_id |
      task_id | agent_run_id | decision | reason_code)
  seq_no 通过 Redis INCR 原子递增，保证全局唯一且有序；Redis 不可用时降级为
  DB max(seq_no)+1（需外部串行化保证，残余风险见交付文档）。

写入约束：
- 追加式：业务代码只允许调用 write_event；无 UPDATE/DELETE 服务路径。
- 写失败策略：OBS_AUDIT_FAILURE_DEFAULT_ACTION（默认 degrade：记录降级日志并返回
  None，不吞错）；OBS_AUDIT_FAILURE_BLOCK_EVENT_TYPES_JSON 中的事件类（默认
  export / permission_change / sign_callback）写失败抛 AuditWriteError（fail-closed）。
- 落库字段全部脱敏：sanitized_metadata 仅允许摘要；不存正文/密钥/PII。
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import redis as redis_lib
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.legal_notifications import SecurityAuditEvent

logger = logging.getLogger(__name__)

_SEQ_KEY = "security_audit:seq_no"

SCHEMA_VERSION_V1 = 1
SCHEMA_VERSION_V2 = 2
_CURRENT_SCHEMA_VERSION = SCHEMA_VERSION_V2

# 校验分页批次（避免一次载入全部事件）。
_VERIFY_BATCH_SIZE = 500


class AuditWriteError(RuntimeError):
    """高风险审计事件写入失败（fail-closed 语义，调用方按阻断处理）。"""


def _next_seq_no(db: Session = None) -> int:
    """Redis INCR 获取序号；Redis 不可用时 DB max+1 降级（可复用调用方 db，
    避免在测试/事务上下文中误连独立连接）。"""
    try:
        r = redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)
        return int(r.incr(_SEQ_KEY))
    except Exception:
        own = db is None
        if own:
            db = SessionLocal()
        try:
            row = db.query(SecurityAuditEvent).order_by(
                SecurityAuditEvent.seq_no.desc()
            ).first()
            return (row.seq_no + 1) if row else 1
        finally:
            if own:
                db.close()


def _normalize_occurred_at(occurred_at: datetime) -> datetime:
    """统一归一化为 naive UTC：DB 读回 DateTime(timezone=True) 会丢失 tzinfo，
    写入与校验必须使用同一表示，否则哈希必然不匹配。"""
    now = occurred_at or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    return now


def _compute_hash(
    seq_no: int,
    event_type: str,
    actor_id: str,
    occurred_at: datetime,
    prev_hash: Optional[str],
    *,
    schema_version: int = SCHEMA_VERSION_V1,
    action: Optional[str] = None,
    resource_version: Optional[str] = None,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    agent_run_id: Optional[int] = None,
    decision: Optional[str] = None,
    reason_code: Optional[str] = None,
) -> str:
    if schema_version == SCHEMA_VERSION_V1:
        raw = f"{seq_no}|{event_type}|{actor_id}|{occurred_at.isoformat()}|{prev_hash or ''}"
    else:
        raw = "|".join([
            "v2",
            str(seq_no),
            event_type,
            actor_id or "",
            occurred_at.isoformat(),
            prev_hash or "",
            action or "",
            resource_version or "",
            request_id or "",
            trace_id or "",
            task_id or "",
            str(agent_run_id) if agent_run_id is not None else "",
            decision or "",
            reason_code or "",
        ])
    return hashlib.sha256(raw.encode()).hexdigest()


def audit_failure_action(event_type: str) -> str:
    """审计写失败策略：'block'（抛 AuditWriteError）或 'degrade'（记录并返回 None）。"""
    settings = get_settings()
    try:
        block_types = json.loads(settings.OBS_AUDIT_FAILURE_BLOCK_EVENT_TYPES_JSON or "[]")
    except (TypeError, ValueError):
        block_types = []
    if event_type in block_types:
        return "block"
    return settings.OBS_AUDIT_FAILURE_DEFAULT_ACTION or "degrade"


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
    # ── P1 扩展字段（schema_version=2 起参与哈希）────────────────────────────
    action: Optional[str] = None,
    resource_version: Optional[str] = None,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    task_id: Optional[str] = None,
    agent_run_id: Optional[int] = None,
    decision: Optional[str] = None,
    reason_code: Optional[str] = None,
    sanitized_metadata: Optional[str] = None,
    audit_id: Optional[str] = None,
) -> Optional[SecurityAuditEvent]:
    """写入一条审计事件，返回写入的事件对象。

    - 失败策略见模块 docstring：block 类抛 AuditWriteError，degrade 类记录降级日志
      并返回 None（绝不静默吞错）。
    - request_id/trace_id/task_id/agent_run_id 未显式传入时从统一上下文补齐。
    """
    # 从统一上下文补齐关联字段（仅当调用方未显式提供）。
    if request_id is None or trace_id is None or task_id is None or agent_run_id is None:
        try:
            from app.core.obs_context import get_context

            ctx = get_context()
            if request_id is None:
                request_id = ctx.request_id
            if trace_id is None:
                trace_id = ctx.trace_id
            if task_id is None:
                task_id = ctx.task_id
            if agent_run_id is None:
                agent_run_id = ctx.agent_run_id
        except Exception:  # noqa: BLE001 - 上下文缺失不影响审计主体
            pass

    _own_db = db is None
    if _own_db:
        db = SessionLocal()
    try:
        now = _normalize_occurred_at(occurred_at)
        seq_no = _next_seq_no(db)

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
            schema_version=_CURRENT_SCHEMA_VERSION,
            action=action,
            resource_version=resource_version,
            request_id=request_id,
            trace_id=trace_id,
            task_id=task_id,
            agent_run_id=agent_run_id,
            decision=decision,
            reason_code=reason_code,
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
            audit_id=audit_id or str(seq_no),
            action=action,
            resource_version=resource_version,
            request_id=request_id,
            trace_id=trace_id,
            task_id=task_id,
            agent_run_id=agent_run_id,
            decision=decision,
            reason_code=reason_code,
            sanitized_metadata=sanitized_metadata,
            schema_version=_CURRENT_SCHEMA_VERSION,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event
    except Exception as exc:  # noqa: BLE001 - 按策略处理，绝不静默
        try:
            db.rollback()
        except Exception:
            pass
        logger.error("security audit write failed event_type=%s: %s: %s",
                     event_type, type(exc).__name__, exc)
        try:
            from app.core.observability import log_security_event

            log_security_event(
                event_name="audit_write_failed",
                outcome="error",
                error_code=type(exc).__name__,
                detail=f"event_type={event_type}; action=degrade_or_block",
                actor=actor_id,
                extra={"event_type": event_type, "result": result},
            )
        except Exception:  # noqa: BLE001 - 降级日志失败不再抛出
            pass
        if audit_failure_action(event_type) == "block":
            raise AuditWriteError(f"审计写入失败（fail-closed）：event_type={event_type}") from exc
        return None
    finally:
        if _own_db:
            db.close()


def verify_chain(organization_id: Optional[int] = None, batch_size: int = _VERIFY_BATCH_SIZE) -> dict:
    """分页校验哈希链完整性，返回断链/重复/时间异常位置（空列表=完整）。

    - 断链：按行 schema_version 重算哈希不匹配。
    - 重复：seq_no 重复（理论上被 UNIQUE 约束禁止，双保险检测）与 audit_id 重复。
    - 时间异常：occurred_at 相对前驱回退。
    """
    db = SessionLocal()
    try:
        broken: list[dict] = []
        duplicate_seq: list[int] = []
        duplicate_audit_ids: list[str] = []
        time_anomalies: list[dict] = []
        total = 0
        prev_hash: Optional[str] = None
        prev_occurred_at: Optional[datetime] = None
        seen_seq: set[int] = set()
        seen_audit_ids: set[str] = set()

        last_seq = 0
        while True:
            q = db.query(SecurityAuditEvent).filter(SecurityAuditEvent.seq_no > last_seq)
            if organization_id is not None:
                q = q.filter(SecurityAuditEvent.organization_id == organization_id)
            batch = q.order_by(SecurityAuditEvent.seq_no).limit(batch_size).all()
            if not batch:
                break
            for ev in batch:
                total += 1
                if ev.seq_no in seen_seq:
                    duplicate_seq.append(ev.seq_no)
                seen_seq.add(ev.seq_no)
                aid = ev.audit_id or str(ev.seq_no)
                if aid in seen_audit_ids:
                    duplicate_audit_ids.append(aid)
                seen_audit_ids.add(aid)
                if prev_occurred_at is not None and ev.occurred_at < prev_occurred_at:
                    time_anomalies.append({"seq_no": ev.seq_no, "id": ev.id,
                                           "occurred_at": ev.occurred_at.isoformat()})
                # 写入时 prev_hash 引用的是全局 seq_no 前驱（可能属于其他组织），
                # 按组织过滤后必须回到全局序列取前驱，否则会把正常事件误判为断链；
                # 全局校验时用跨批跟踪的 prev_hash（分页不断链）。
                if organization_id is not None:
                    prev = db.query(SecurityAuditEvent).filter(
                        SecurityAuditEvent.seq_no < ev.seq_no
                    ).order_by(SecurityAuditEvent.seq_no.desc()).first()
                    expected_prev_hash = prev.current_hash if prev is not None else None
                else:
                    expected_prev_hash = prev_hash
                expected = _compute_hash(
                    seq_no=ev.seq_no,
                    event_type=ev.event_type,
                    actor_id=ev.actor_id or "",
                    occurred_at=ev.occurred_at,
                    prev_hash=expected_prev_hash,
                    schema_version=int(ev.schema_version or SCHEMA_VERSION_V1),
                    action=ev.action,
                    resource_version=ev.resource_version,
                    request_id=ev.request_id,
                    trace_id=ev.trace_id,
                    task_id=ev.task_id,
                    agent_run_id=ev.agent_run_id,
                    decision=ev.decision,
                    reason_code=ev.reason_code,
                )
                if expected != ev.current_hash:
                    broken.append({"seq_no": ev.seq_no, "id": ev.id})
                prev_hash = ev.current_hash
                prev_occurred_at = ev.occurred_at
                last_seq = ev.seq_no

        return {
            "total": total,
            "checked": total,
            "broken": broken,
            "duplicate_seq_no": duplicate_seq,
            "duplicate_audit_ids": duplicate_audit_ids,
            "time_anomalies": time_anomalies,
            "intact": not (broken or duplicate_seq or duplicate_audit_ids or time_anomalies),
        }
    finally:
        db.close()


security_audit_service = type("_Svc", (), {
    "write_event": staticmethod(write_event),
    "verify_chain": staticmethod(verify_chain),
    "audit_failure_action": staticmethod(audit_failure_action),
    "AuditWriteError": AuditWriteError,
})()
