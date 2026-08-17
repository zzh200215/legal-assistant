"""法律案件管理 API — Phase 9 Week 2

一个案件将咨询、合同审查、文书草稿归档到同一容器，
便于律师/法务管理同一当事人的全套工作。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from app.core.api_response import api_error
from app.core.auth import get_current_user, verify_case_access
from app.core.database import get_db
from app.models.legal import LegalCase, LegalConsultation, ContractReview, LegalDraft
from app.models.org import OrganizationMember, LegalMemberRole
from app.models.user import User

router = APIRouter()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LegalCaseIn(BaseModel):
    organization_id: int
    title: str = Field(min_length=1, max_length=256)
    case_type: str = Field(default="other")
    client_name: Optional[str] = Field(default=None, max_length=128)
    opposing_party: Optional[str] = Field(default=None, max_length=256)
    description: Optional[str] = Field(default=None, max_length=4000)
    is_strict_mode: bool = False


class LegalCaseUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=256)
    case_type: Optional[str] = None
    status: Optional[str] = None
    client_name: Optional[str] = Field(default=None, max_length=128)
    opposing_party: Optional[str] = Field(default=None, max_length=256)
    description: Optional[str] = Field(default=None, max_length=4000)
    is_strict_mode: Optional[bool] = None


VALID_CASE_TYPES = {"labor_dispute", "contract_dispute", "private_lending", "consumer_dispute", "other"}
VALID_STATUSES = {"in_progress", "closed", "archived"}


def _require_org_member(db: Session, user_id: int, org_id: int) -> OrganizationMember:
    """验证用户是组织成员（任意角色）。"""
    member = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.user_id == user_id,
    ).first()
    if not member:
        raise api_error(403, "不是该组织成员", code="NOT_ORG_MEMBER")
    return member


def _require_editor_or_above(db: Session, user_id: int, org_id: int) -> OrganizationMember:
    """验证用户是 editor / reviewer / admin 才能创建和修改案件。"""
    member = _require_org_member(db, user_id, org_id)
    if member.legal_role == LegalMemberRole.client.value:
        raise api_error(403, "客户角色无法管理案件", code="INSUFFICIENT_ROLE")
    return member


def _serialize_case(case: LegalCase, db: Session) -> dict:
    consultations = db.query(LegalConsultation).filter(LegalConsultation.case_id == case.id).count()
    reviews = db.query(ContractReview).filter(ContractReview.case_id == case.id).count()
    drafts = db.query(LegalDraft).filter(LegalDraft.case_id == case.id).count()
    return {
        "id": case.id,
        "organization_id": case.organization_id,
        "user_id": case.user_id,
        "title": case.title,
        "case_type": case.case_type,
        "status": case.status,
        "is_strict_mode": bool(case.is_strict_mode),
        "client_name": case.client_name,
        "opposing_party": case.opposing_party,
        "description": case.description,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "item_counts": {"consultations": consultations, "reviews": reviews, "drafts": drafts},
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/orgs/{org_id}/cases")
def list_cases(
    org_id: int,
    status: Optional[str] = None,
    case_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_org_member(db, current_user.id, org_id)
    from app.services.org.authorization_service import (
        PermissionAction,
        authorization_service,
    )

    ctx = authorization_service.build_context(db, current_user, org_id=org_id)
    q = authorization_service.scope_query(
        db, LegalCase, ctx, PermissionAction.CASE_READ, org_id=org_id
    )
    if status:
        q = q.filter(LegalCase.status == status)
    if case_type:
        q = q.filter(LegalCase.case_type == case_type)
    cases = q.order_by(LegalCase.updated_at.desc()).all()
    return [_serialize_case(c, db) for c in cases]


@router.post("/orgs/{org_id}/cases", status_code=201)
def create_case(
    org_id: int,
    req: LegalCaseIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if req.organization_id != org_id:
        raise api_error(400, "organization_id 与路径不匹配", code="ORG_MISMATCH")
    member = _require_editor_or_above(db, current_user.id, org_id)
    if req.is_strict_mode and member.legal_role not in (LegalMemberRole.admin.value, LegalMemberRole.reviewer.value):
        raise api_error(403, "仅审核律师或管理员可创建严格案件", code="INSUFFICIENT_ROLE")
    if req.case_type not in VALID_CASE_TYPES:
        raise api_error(400, f"无效的 case_type，支持: {VALID_CASE_TYPES}", code="INVALID_CASE_TYPE")
    case = LegalCase(
        organization_id=org_id,
        user_id=current_user.id,
        title=req.title.strip(),
        case_type=req.case_type,
        client_name=req.client_name,
        opposing_party=req.opposing_party,
        description=req.description,
        is_strict_mode=int(req.is_strict_mode),
    )
    db.add(case)
    db.flush()
    # 所有案件创建时写入 owner，严格模式不会因创建者缺少成员记录而锁死。
    from app.models.legal_portal import LegalCaseMember
    db.add(LegalCaseMember(
        case_id=case.id, organization_id=org_id, user_id=current_user.id,
        case_role="owner", granted_by=current_user.id,
    ))
    db.commit()
    db.refresh(case)
    return _serialize_case(case, db)


@router.get("/orgs/{org_id}/cases/{case_id}")
def get_case(
    org_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_org_member(db, current_user.id, org_id)
    case = db.query(LegalCase).filter(
        LegalCase.id == case_id,
        LegalCase.organization_id == org_id,
    ).first()
    if not case:
        raise api_error(404, "案件不存在", code="CASE_NOT_FOUND")
    # 统一授权：严格案件仅活跃成员（404）；client 仅自己/活跃成员案件（403）。
    from app.services.org.authorization_service import (
        PermissionAction,
        authorization_service,
    )

    ctx = authorization_service.build_context(db, current_user, org_id=org_id)
    authorization_service.require(db, ctx, PermissionAction.CASE_READ, case=case)
    return _serialize_case(case, db)


@router.patch("/orgs/{org_id}/cases/{case_id}")
def update_case(
    org_id: int,
    case_id: int,
    req: LegalCaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = _require_editor_or_above(db, current_user.id, org_id)
    case = db.query(LegalCase).filter(
        LegalCase.id == case_id,
        LegalCase.organization_id == org_id,
    ).first()
    if not case:
        raise api_error(404, "案件不存在", code="CASE_NOT_FOUND")
    verify_case_access(case_id, current_user.id, db)
    if req.status and req.status not in VALID_STATUSES:
        raise api_error(400, f"无效的 status，支持: {VALID_STATUSES}", code="INVALID_STATUS")
    if req.case_type and req.case_type not in VALID_CASE_TYPES:
        raise api_error(400, f"无效的 case_type", code="INVALID_CASE_TYPE")
    if req.is_strict_mode is not None and member.legal_role not in (LegalMemberRole.admin.value, LegalMemberRole.reviewer.value):
        raise api_error(403, "仅审核律师或管理员可切换严格模式", code="INSUFFICIENT_ROLE")
    for field, value in req.model_dump(exclude_none=True).items():
        setattr(case, field, value)
    db.commit()
    db.refresh(case)
    return _serialize_case(case, db)


@router.get("/orgs/{org_id}/cases/{case_id}/items")
def list_case_items(
    org_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取案件下所有工作记录（咨询+审查+文书）。"""
    _require_org_member(db, current_user.id, org_id)
    case = db.query(LegalCase).filter(
        LegalCase.id == case_id,
        LegalCase.organization_id == org_id,
    ).first()
    if not case:
        raise api_error(404, "案件不存在", code="CASE_NOT_FOUND")
    if getattr(case, "is_strict_mode", 0):
        from app.models.legal_portal import LegalCaseMember
        case_member = db.query(LegalCaseMember).filter(
            LegalCaseMember.case_id == case_id, LegalCaseMember.user_id == current_user.id,
            LegalCaseMember.revoked_at.is_(None),
        ).first()
        if not case_member:
            raise api_error(404, "案件不存在", code="CASE_NOT_FOUND")

    consultations = db.query(LegalConsultation).filter(LegalConsultation.case_id == case_id).all()
    reviews = db.query(ContractReview).filter(ContractReview.case_id == case_id).all()
    drafts = db.query(LegalDraft).filter(LegalDraft.case_id == case_id).all()

    return {
        "case_id": case_id,
        "consultations": [
            {"id": c.id, "question": c.question[:100], "category": c.category,
             "risk_level": c.risk_level, "status": c.status, "created_at": c.created_at}
            for c in consultations
        ],
        "contract_reviews": [
            {"id": r.id, "title": r.title, "status": r.status, "created_at": r.created_at}
            for r in reviews
        ],
        "drafts": [
            {"id": d.id, "document_type": d.document_type, "title": d.title,
             "status": d.status, "created_at": d.created_at}
            for d in drafts
        ],
    }
