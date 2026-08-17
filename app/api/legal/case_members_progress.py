"""案件成员 + 客户进度更新子路由。"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.legal._portal_helpers import (
    _require_case_manager,
    _require_organization_member,
    _require_progress_editor,
)
from app.core.auth import get_current_user, verify_case_access
from app.core.database import get_db
from app.models.legal_portal import LegalCaseMember, LegalCaseProgressUpdate
from app.models.user import User

router = APIRouter()

class CaseMemberCreate(BaseModel):
    user_id: int
    case_role: str = Field(..., pattern="^(owner|collaborator|viewer|client_contact)$")


@router.get("/orgs/{org_id}/cases/{case_id}/members")
def list_case_members(
    org_id: int,
    case_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    members = db.query(LegalCaseMember).filter(
        LegalCaseMember.case_id == case_id,
        LegalCaseMember.organization_id == org_id,
        LegalCaseMember.revoked_at.is_(None),
    ).all()
    return members


@router.post("/orgs/{org_id}/cases/{case_id}/members")
def add_case_member(
    org_id: int,
    case_id: int,
    body: CaseMemberCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    _require_organization_member(db, org_id, body.user_id)
    existing = db.query(LegalCaseMember).filter(
        LegalCaseMember.case_id == case_id,
        LegalCaseMember.organization_id == org_id,
        LegalCaseMember.user_id == body.user_id,
        LegalCaseMember.revoked_at.is_(None),
    ).first()
    if existing:
        raise HTTPException(409, detail="该成员已在案件中")

    member = LegalCaseMember(
        case_id=case_id,
        organization_id=org_id,
        user_id=body.user_id,
        case_role=body.case_role,
        granted_by=current_user.id,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.patch("/case-members/{member_id}")
def patch_case_member(
    member_id: int,
    case_role: Optional[str] = None,
    revoke: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = db.query(LegalCaseMember).filter(LegalCaseMember.id == member_id).first()
    if not member:
        raise HTTPException(404)
    _require_case_manager(db, current_user.id, member.organization_id, member.case_id)
    if revoke:
        member.revoked_at = datetime.now(timezone.utc)
    if case_role:
        member.case_role = case_role
    db.commit()
    db.refresh(member)
    return member


class ProgressUpdateCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=128)
    body: str = Field(..., min_length=1, max_length=5000)
    next_steps: Optional[str] = Field(None, max_length=1000)
    visibility: str = Field("internal", pattern="^(internal|client_visible)$")


@router.post("/orgs/{org_id}/cases/{case_id}/progress-updates")
def create_progress_update(
    org_id: int,
    case_id: int,
    body: ProgressUpdateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = _require_progress_editor(db, current_user.id, org_id, case_id)
    update = LegalCaseProgressUpdate(
        case_id=case_id,
        organization_id=case.organization_id,
        title=body.title,
        body=body.body,
        next_steps=body.next_steps,
        visibility=body.visibility,
        # 对客户可见内容一律走审核；内部记录可保留草稿。
        status="pending_review" if body.visibility == "client_visible" else "draft",
        created_by=current_user.id,
    )
    db.add(update)
    db.commit()
    db.refresh(update)
    return update


@router.post("/progress-updates/{update_id}/publish")
def publish_progress_update(
    update_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    update = db.query(LegalCaseProgressUpdate).filter(
        LegalCaseProgressUpdate.id == update_id
    ).first()
    if not update:
        raise HTTPException(404)
    member = _require_organization_member(db, update.organization_id, current_user.id)
    if member.legal_role not in ("admin", "reviewer"):
        raise HTTPException(403, detail="仅审核律师或管理员可发布客户进度")
    # 严格案件的活跃成员校验：被撤销成员即使保留组织身份也不得发布（404 隐藏存在性）。
    try:
        verify_case_access(update.case_id, current_user.id, db)
    except Exception:
        raise HTTPException(404, detail="进度更新不存在")
    if update.visibility != "client_visible" or update.status != "pending_review":
        raise HTTPException(409, detail=f"Cannot publish a {update.status} update")
    update.status = "published"
    update.published_at = datetime.now(timezone.utc)
    update.reviewed_by = current_user.id
    db.commit()
    db.refresh(update)
    return update


@router.post("/progress-updates/{update_id}/withdraw")
def withdraw_progress_update(
    update_id: int,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    update = db.query(LegalCaseProgressUpdate).filter(
        LegalCaseProgressUpdate.id == update_id
    ).first()
    if not update:
        raise HTTPException(404)
    member = _require_organization_member(db, update.organization_id, current_user.id)
    if member.legal_role not in ("admin", "reviewer"):
        raise HTTPException(403, detail="仅审核律师或管理员可撤回客户进度")
    # 严格案件的活跃成员校验：被撤销成员即使保留组织身份也不得撤回（404 隐藏存在性）。
    try:
        verify_case_access(update.case_id, current_user.id, db)
    except Exception:
        raise HTTPException(404, detail="进度更新不存在")
    if update.status != "published":
        raise HTTPException(409, detail="只能撤回已发布的更新")
    update.status = "withdrawn"
    update.withdrawn_at = datetime.now(timezone.utc)
    update.withdraw_reason = reason
    db.commit()
    db.refresh(update)
    return update


@router.get("/orgs/{org_id}/cases/{case_id}/progress-updates")
def list_progress_updates(
    org_id: int,
    case_id: int,
    visibility: Optional[str] = None,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    q = db.query(LegalCaseProgressUpdate).filter(
        LegalCaseProgressUpdate.case_id == case_id,
        LegalCaseProgressUpdate.organization_id == org_id,
    )
    if visibility:
        q = q.filter(LegalCaseProgressUpdate.visibility == visibility)
    total = q.count()
    items = q.order_by(LegalCaseProgressUpdate.created_at.desc(), LegalCaseProgressUpdate.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [
            {
                "id": u.id,
                "case_id": u.case_id,
                "organization_id": u.organization_id,
                "title": u.title,
                "body": u.body,
                "next_steps": u.next_steps,
                "visibility": u.visibility,
                "status": u.status,
                "created_by": u.created_by,
                "reviewed_by": u.reviewed_by,
                "published_at": u.published_at,
                "created_at": u.created_at,
                "updated_at": u.updated_at,
                "withdrawn_at": u.withdrawn_at,
                "withdraw_reason": u.withdraw_reason,
            }
            for u in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }

