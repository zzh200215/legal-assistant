"""#95/账号注销 API：冷却期状态机（请求/撤销/确认）+ 管理端列表与强制确认"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error, paginated_payload
from app.core.auth import get_current_user, require_admin_user
from app.core.database import get_db
from app.models.user import User, UserStatus
from app.services.account_deletion_service import (
    DELETION_COOL_DOWN_DAYS,
    cancel_deletion,
    confirm_deletion,
    request_deletion,
)

router = APIRouter()


class DeletionNote(BaseModel):
    note: str | None = Field(None, max_length=2000)


@router.post("/account-deletion/request")
def request_account_deletion(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = request_deletion(db, current_user)
    from app.models.user import User as _U  # noqa: F401
    return {
        "status": user.status,
        "cool_down_days": DELETION_COOL_DOWN_DAYS,
        "requested_at": str(user.deletion_requested_at),
        "message": f"注销请求已受理，{DELETION_COOL_DOWN_DAYS} 天冷却期内可撤销；确认后账户数据将匿名化。",
    }


@router.post("/account-deletion/cancel")
def cancel_account_deletion(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = cancel_deletion(db, current_user)
    return {"status": user.status}


@router.post("/account-deletion/confirm")
def confirm_account_deletion(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        user = confirm_deletion(db, current_user)
    except ValueError as exc:
        raise api_error(400, str(exc), code="DELETION_COOL_DOWN_ACTIVE")
    return {"status": user.status, "confirmed_at": str(user.deletion_confirmed_at)}


@router.get("/admin/account-deletions")
def list_pending_deletions(
    status: str = Query("deletion_pending", description="deletion_pending / deleted / all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    q = db.query(User)
    if status != "all":
        q = q.filter(User.status == status)
    total = q.count()
    rows = (
        q.order_by(User.deletion_requested_at.desc().nullslast())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for u in rows:
        requested = u.deletion_requested_at
        if requested and requested.tzinfo is None:
            requested = requested.replace(tzinfo=timezone.utc)
        remaining = None
        if requested:
            import datetime as _dt
            remaining = max(0, DELETION_COOL_DOWN_DAYS - (datetime.now(timezone.utc) - requested).days)
        items.append({
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "status": u.status,
            "requested_at": str(requested) if requested else None,
            "remaining_days": remaining,
            "confirmed_at": str(u.deletion_confirmed_at) if u.deletion_confirmed_at else None,
        })
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.post("/admin/account-deletions/{user_id}/confirm")
def admin_confirm_deletion(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise api_error(404, "用户不存在", code="USER_NOT_FOUND")
    user = confirm_deletion(db, user, force=True)
    return {"id": user.id, "status": user.status, "confirmed_at": str(user.deletion_confirmed_at)}
