"""门户品牌 + 关键日期（Deadline）子路由。"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.legal._portal_helpers import _require_case_manager, _require_organization_member
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.legal_portal import LegalDeadline
from app.models.org import Organization
from app.models.user import User

router = APIRouter()

class PortalBrandingIn(BaseModel):
    portal_logo_url: str | None = Field(None, max_length=512)
    portal_welcome_message: str | None = Field(None, max_length=256)


@router.get("/orgs/{org_id}/portal-branding")
def get_portal_branding(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_organization_member(db, org_id, current_user.id)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, detail="组织不存在")
    return {
        "organization_id": org.id,
        "name": org.name,
        "portal_logo_url": org.portal_logo_url,
        "portal_welcome_message": org.portal_welcome_message,
    }


@router.put("/orgs/{org_id}/portal-branding")
def update_portal_branding(
    org_id: int,
    body: PortalBrandingIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = _require_organization_member(db, org_id, current_user.id)
    if member.legal_role not in ("admin", "reviewer"):
        raise HTTPException(403, detail="仅组织管理员或审核律师可配置门户品牌")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(404, detail="组织不存在")
    org.portal_logo_url = (body.portal_logo_url or "").strip() or None
    org.portal_welcome_message = (body.portal_welcome_message or "").strip() or None
    db.commit()
    return {
        "organization_id": org.id,
        "name": org.name,
        "portal_logo_url": org.portal_logo_url,
        "portal_welcome_message": org.portal_welcome_message,
    }


class DeadlineCreate(BaseModel):
    deadline_type: str = Field(..., pattern="^(hearing|defense|appeal|performance|payment|expiry|custom)$")
    deadline_at: datetime
    timezone: str = "Asia/Shanghai"
    owner_id: int
    description: Optional[str] = None
    reminder_offsets_json: Optional[str] = None  # JSON array, e.g. "[7,3,1]"
    is_historical: int = 0


@router.post("/orgs/{org_id}/cases/{case_id}/deadlines")
def create_deadline(
    org_id: int,
    case_id: int,
    body: DeadlineCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    _require_organization_member(db, org_id, body.owner_id)
    deadline = LegalDeadline(
        organization_id=org_id,
        case_id=case_id,
        deadline_type=body.deadline_type,
        deadline_at=body.deadline_at,
        timezone=body.timezone,
        owner_id=body.owner_id,
        description=body.description,
        reminder_offsets_json=body.reminder_offsets_json or "[7,3,1]",
        is_historical=body.is_historical,
        created_by=current_user.id,
    )
    db.add(deadline)
    db.commit()
    db.refresh(deadline)
    return deadline


class DeadlinePatch(BaseModel):
    action: Optional[str] = None  # complete / cancel
    deadline_at: Optional[datetime] = None
    owner_id: Optional[int] = None
    description: Optional[str] = None
    reminder_offsets_json: Optional[str] = None


@router.patch("/deadlines/{deadline_id}")
def patch_deadline(
    deadline_id: int,
    body: DeadlinePatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    dl = db.query(LegalDeadline).filter(LegalDeadline.id == deadline_id).first()
    if not dl:
        raise HTTPException(404)
    if not dl.case_id:
        raise HTTPException(404)
    _require_case_manager(db, current_user.id, dl.organization_id, dl.case_id)
    if body.action == "complete":
        dl.status = "completed"
    elif body.action == "cancel":
        dl.status = "cancelled"
    if body.deadline_at:
        dl.deadline_at = body.deadline_at
    if body.owner_id is not None:
        _require_organization_member(db, dl.organization_id, body.owner_id)
        dl.owner_id = body.owner_id
    if body.description is not None:
        dl.description = body.description
    if body.reminder_offsets_json is not None:
        dl.reminder_offsets_json = body.reminder_offsets_json
    db.commit()
    db.refresh(dl)
    return dl


@router.post("/deadlines/{deadline_id}/calendar-suggestion")
def deadline_to_calendar(
    deadline_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """将关键日期转为 CalendarSuggestion，复用现有 ICS 下载路径。"""
    from datetime import timedelta

    from app.models.calendar import CalendarSuggestion

    dl = db.query(LegalDeadline).filter(LegalDeadline.id == deadline_id).first()
    if not dl:
        raise HTTPException(404)
    if not dl.case_id:
        raise HTTPException(404)
    _require_case_manager(db, current_user.id, dl.organization_id, dl.case_id)

    type_labels = {
        "hearing": "开庭", "defense": "答辩", "appeal": "上诉",
        "performance": "履行", "payment": "付款", "expiry": "到期", "custom": "自定义",
    }
    label = type_labels.get(dl.deadline_type, dl.deadline_type)

    suggestion = CalendarSuggestion(
        user_id=current_user.id,
        title=f"[{label}] {dl.description or '案件关键日期'}",
        description=f"案件关键日期 - 类型：{label}\n备注：{dl.description or ''}",
        starts_at=dl.deadline_at,
        ends_at=dl.deadline_at + timedelta(hours=1),
        attendees="[]",
        status="pending",
    )
    db.add(suggestion)
    db.commit()
    db.refresh(suggestion)
    return {"suggestion_id": suggestion.id, "title": suggestion.title,
            "starts_at": suggestion.starts_at}


@router.get("/orgs/{org_id}/cases/{case_id}/deadlines")
def list_deadlines(
    org_id: int,
    case_id: int,
    status: Optional[str] = None,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_case_manager(db, current_user.id, org_id, case_id)
    q = db.query(LegalDeadline).filter(
        LegalDeadline.organization_id == org_id,
        LegalDeadline.case_id == case_id,
    )
    if status:
        q = q.filter(LegalDeadline.status == status)
    total = q.count()
    items = q.order_by(LegalDeadline.deadline_at, LegalDeadline.id).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [
            {
                "id": d.id,
                "organization_id": d.organization_id,
                "case_id": d.case_id,
                "contract_id": d.contract_id,
                "deadline_type": d.deadline_type,
                "deadline_at": d.deadline_at,
                "timezone": d.timezone,
                "owner_id": d.owner_id,
                "status": d.status,
                "description": d.description,
                "source_milestone_id": d.source_milestone_id,
                "is_historical": d.is_historical,
                "created_by": d.created_by,
                "created_at": d.created_at,
                "updated_at": d.updated_at,
            }
            for d in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }

