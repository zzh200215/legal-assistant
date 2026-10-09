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
    user.organization_id = None
    user.department_id = None
    user.force_password_change = True
    user.token_version = (user.token_version or 0) + 1
    user.status = UserStatus.deleted.value
    user.deletion_confirmed_at = datetime.now(timezone.utc)
    db.add(user)


def _anonymize_business_fields(db: Session, user_id: int) -> None:
    """Wipe encrypted personal content while retaining legal and billing records."""
    from app.models.legal import LegalCase, LegalConsultation, ContractReview, LegalDraft
    from app.models.legal import LegalDocumentVersion
    from app.models.legal_billing import LegalInvoice
    from app.models.legal_contract import LegalContract, LegalContractClause, LegalContractVersion

    case_query = db.query(LegalCase).filter(LegalCase.user_id == user_id)
    case_ids = [case_id for (case_id,) in case_query.with_entities(LegalCase.id).all()]
    for case in case_query.all():
        case.title = f"已匿名案件#{case.id}"
        case.client_name = None
        case.opposing_party = None
        case.description = None

    db.query(LegalConsultation).filter(LegalConsultation.user_id == user_id).update(
        {"reviewer_id": None, "review_note": None}
    )
    reviews = db.query(ContractReview).filter(ContractReview.user_id == user_id).all()
    drafts = db.query(LegalDraft).filter(LegalDraft.user_id == user_id).all()
    for review in reviews:
        review.title = f"已匿名合同审查#{review.id}"
        review.content = ""
        review.reviewer_id = None
        review.review_note = None
        review.feedback_note = None
    for draft in drafts:
        draft.title = f"已匿名文书#{draft.id}"
        draft.content = ""
        draft.fields_json = "{}"
        draft.reviewer_id = None
        draft.review_note = None
        draft.feedback_note = None

    review_ids = [review.id for review in reviews]
    draft_ids = [draft.id for draft in drafts]
    version_query = db.query(LegalDocumentVersion).filter(
        ((LegalDocumentVersion.target_type == "contract_review") & LegalDocumentVersion.target_id.in_(review_ids))
        | ((LegalDocumentVersion.target_type == "draft") & LegalDocumentVersion.target_id.in_(draft_ids))
    ) if review_ids or draft_ids else None
    if version_query is not None:
        for version in version_query.all():
            version.title = "已匿名文书版本"
            version.content = ""

    contracts = db.query(LegalContract).filter(
        (LegalContract.created_by == user_id) | (LegalContract.responsible_user_id == user_id)
    ).all()
    contract_ids = [contract.id for contract in contracts]
    for contract in contracts:
        contract.description = None
        contract.responsible_user_id = None
    contract_versions = db.query(LegalContractVersion).filter(
        (LegalContractVersion.created_by == user_id)
        | (LegalContractVersion.contract_id.in_(contract_ids) if contract_ids else False)
    ).all()
    version_ids = [version.id for version in contract_versions]
    for version in contract_versions:
        version.text_snapshot = None
    if version_ids:
        db.query(LegalContractClause).filter(LegalContractClause.contract_version_id.in_(version_ids)).update(
            {"content": ""}, synchronize_session=False
        )

    invoice_query = db.query(LegalInvoice).filter(
        (LegalInvoice.created_by == user_id)
        | (LegalInvoice.case_id.in_(case_ids) if case_ids else False)
    )
    for invoice in invoice_query.all():
        invoice.client_display_name = "已匿名客户"
        invoice.client_contact = None


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


def confirm_expired_pending(db: Session, *, force_days: int = 30, batch_size: int = 100) -> int:
    """Confirm expired requests in bounded batches for scheduled processing."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    cutoff = datetime.now(timezone.utc) - timedelta(days=force_days)
    confirmed = 0
    while True:
        rows = (
            db.query(User)
            .filter(
                User.status == UserStatus.deletion_pending.value,
                User.deletion_requested_at.isnot(None),
                User.deletion_requested_at < cutoff,
            )
            .order_by(User.deletion_requested_at, User.id)
            .limit(batch_size)
            .all()
        )
        if not rows:
            return confirmed
        for user in rows:
            confirm_deletion(db, user, force=True)
            confirmed += 1
