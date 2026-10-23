"""审核 SLA 站内提醒：到期待审记录给审核人发一次「即将到期 / 已逾期」通知。

由 Celery beat 每 15 分钟调用 `check_legal_review_sla`，也便于单测直接调用。
去重：同一审查（reference_type + reference_id）同一阶段（body 标记 sla:due_soon / sla:overdue）
只写一条事件；重新分配或改期不会重复刷屏，换阶段（即将到期 → 逾期）会再提醒一次。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.legal import ContractReview, LegalCase, LegalConsultation, LegalDraft
from app.models.legal_notifications import LegalNotificationEvent
from app.models.user import User

DUE_SOON_HOURS = 24
ACTIVE_STATUSES = ("pending_review", "needs_lawyer_review", "needs_facts")
TARGETS = (
    (LegalConsultation, "consultation", "咨询"),
    (ContractReview, "contract_review", "合同审查"),
    (LegalDraft, "draft", "文书"),
)


def _hours_left(due_at: datetime, now: datetime) -> float:
    due = due_at.replace(tzinfo=None) if due_at.tzinfo else due_at
    return (due - now).total_seconds() / 3600


def _org_id_for(db: Session, row) -> int | None:
    """通知事件要求 organization_id：优先案件所属组织，其次提交人所属组织。"""
    case_id = getattr(row, "case_id", None)
    if case_id:
        case = db.get(LegalCase, case_id)
        if case is not None and case.organization_id:
            return case.organization_id
    user = db.get(User, getattr(row, "user_id", None))
    return user.organization_id if user is not None else None


def _humanize(hours: float) -> str:
    value = abs(hours)
    if value < 1:
        return f"{max(1, round(value * 60))} 分钟"
    if value < 48:
        return f"{round(value)} 小时"
    return f"{round(value / 24)} 天"


def scan_review_sla(db: Session, *, now: datetime | None = None) -> dict:
    """扫描到期待审记录并写站内提醒，返回 {created, due_soon, overdue}。"""
    now = now or utc_now()
    created = due_soon = overdue = 0
    for model, kind, label in TARGETS:
        rows = (
            db.query(model)
            .filter(model.review_due_at.isnot(None), model.status.in_(ACTIVE_STATUSES))
            .all()
        )
        for row in rows:
            reviewer_id = getattr(row, "reviewer_id", None)
            if not reviewer_id:
                continue
            hours = _hours_left(row.review_due_at, now)
            if hours < 0:
                stage, prefix = "overdue", "审核已逾期"
            elif hours <= DUE_SOON_HOURS:
                stage, prefix = "due_soon", "审核即将到期"
            else:
                continue
            org_id = _org_id_for(db, row)
            if not org_id:
                continue
            marker = f"sla:{stage}"
            exists = (
                db.query(LegalNotificationEvent)
                .filter(
                    LegalNotificationEvent.reference_type == kind,
                    LegalNotificationEvent.reference_id == row.id,
                    LegalNotificationEvent.body.like(f"%{marker}%"),
                )
                .first()
            )
            if exists:
                continue
            name = getattr(row, "title", None) or getattr(row, "question", None) or f"{label} #{row.id}"
            name = str(name).strip().splitlines()[0][:40]
            when = f"超时 {_humanize(hours)}" if stage == "overdue" else f"剩 {_humanize(hours)}"
            db.add(LegalNotificationEvent(
                organization_id=org_id,
                user_id=reviewer_id,
                case_id=getattr(row, "case_id", None),
                event_type="approval",
                title=f"{prefix}：{name}（{when}）",
                body=f"{marker} · {label} #{row.id} 的审核截止时间 {row.review_due_at:%Y-%m-%d %H:%M}",
                channel="site",
                status="delivered",
                sent_at=now,
                reference_type=kind,
                reference_id=row.id,
                template_key=f"review_sla_{stage}",
            ))
            created += 1
            if stage == "overdue":
                overdue += 1
            else:
                due_soon += 1
    db.commit()
    return {"created": created, "due_soon": due_soon, "overdue": overdue}
