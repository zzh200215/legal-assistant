"""#95/账号注销服务：冷却期状态机 + 主体匿名化

SLA（docs/data-retention-sla-draft.md §2/§4）：
- 注销请求 → 30 天冷却期（可撤销）
- 确认后：A 类账户数据立即物理删除（用户行保留 id 用于 FK 关联，主体字段匿名化），
  业务数据（B/C 类）保留但主体标识抹除。
"""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.database import get_db  # noqa: F401  (类型引用)
from app.models.user import User, UserStatus

DELETION_COOL_DOWN_DAYS = 30


def request_deletion(db: Session, user: User) -> User:
    """发起注销：进入冷却期（deletion_pending）。"""
    if user.status in (UserStatus.deletion_pending.value, UserStatus.deleted.value):
        return user
    user.status = UserStatus.deletion_pending.value
    user.deletion_requested_at = datetime.now(timezone.utc)
    user.deletion_confirmed_at = None
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def cancel_deletion(db: Session, user: User) -> User:
    """冷却期内撤销注销。"""
    if user.status != UserStatus.deletion_pending.value:
        return user
    user.status = UserStatus.active.value
    user.deletion_requested_at = None
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _anonymize_user(db: Session, user: User) -> None:
    """抹除主体标识字段（A 类）。user_id FK 保留以维持业务数据关联。"""
    suffix = secrets.token_hex(4)
    user.username = f"deleted_{suffix}"
    user.email = f"deleted_{suffix}@deleted.local"
    user.full_name = None
    user.hashed_password = None
    user.job_title = None
    user.employee_id = None
    user.external_provider = None
    user.external_user_id = None
    user.last_login_ip = None
    user.force_password_change = True
    user.status = UserStatus.deleted.value
    user.deletion_confirmed_at = datetime.now(timezone.utc)
    db.add(user)


def _anonymize_business_fields(db: Session, user_id: int) -> None:
    """抹除业务数据中的主体标识字段（B/C 类保留数据但去掉可识别信息）。"""
    from app.models.legal import LegalCase, LegalConsultation, ContractReview, LegalDraft

    db.query(LegalCase).filter(LegalCase.user_id == user_id).update(
        {"client_name": None, "opposing_party": None, "description": None}
    )
    db.query(LegalConsultation).filter(LegalConsultation.user_id == user_id).update(
        {"reviewer_id": None, "review_note": None}
    )
    db.query(ContractReview).filter(ContractReview.user_id == user_id).update({"reviewer_id": None})
    db.query(LegalDraft).filter(LegalDraft.user_id == user_id).update({"reviewer_id": None})


def confirm_deletion(db: Session, user: User, *, force: bool = False) -> User:
    """确认注销：冷却期 ≥30 天或管理员强制时执行匿名化。"""
    if user.status == UserStatus.deleted.value:
        return user
    if not force:
        requested = user.deletion_requested_at
        if not requested:
            raise ValueError("未发起注销请求")
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - requested < timedelta(days=DELETION_COOL_DOWN_DAYS):
            remaining = DELETION_COOL_DOWN_DAYS - (datetime.now(timezone.utc) - requested).days
            raise ValueError(f"仍在 {remaining} 天冷却期内，无法确认注销")
    _anonymize_business_fields(db, user.id)
    _anonymize_user(db, user)
    db.commit()
    db.refresh(user)
    return user


def confirm_expired_pending(db: Session, *, force_days: int = 30) -> int:
    """批处理：自动确认所有冷却期已满的注销请求（管理任务/巡检调用）。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=force_days)
    rows = (
        db.query(User)
        .filter(
            User.status == UserStatus.deletion_pending.value,
            User.deletion_requested_at.isnot(None),
            User.deletion_requested_at < cutoff,
        )
        .all()
    )
    for user in rows:
        confirm_deletion(db, user, force=True)
    return len(rows)
