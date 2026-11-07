"""通知偏好 / 通知中心 / 组织 Onboarding 子路由。"""
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.legal_notifications import (
    LegalNotificationEvent,
    LegalNotificationPolicy,
    LegalNotificationPreference,
    OrganizationOnboardingProgress,
)
from app.models.user import User

router = APIRouter()

ALLOWED_CHANNELS = {"site", "email", "wechat", "feishu"}
SUMMARY_FREQUENCIES = {"none", "daily", "weekly"}
_MUTE_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class NotificationPrefUpdate(BaseModel):
    channels_json: Optional[str] = None
    mute_start: Optional[str] = None
    mute_end: Optional[str] = None
    timezone: Optional[str] = Field(None, max_length=64)
    delegate_user_id: Optional[int] = None
    summary_frequency: Optional[str] = None


def _normalize_mute(value: Optional[str], field: str) -> Optional[str]:
    """空串/None 表示清除静默；只接受 HH:MM。"""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if not _MUTE_TIME_RE.match(text):
        raise HTTPException(422, detail=f"{field} 必须是 HH:MM")
    return text


@router.get("/notification-preferences/me")
def get_notification_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(LegalNotificationPreference).filter(
        LegalNotificationPreference.user_id == current_user.id,
    )
    if current_user.organization_id:
        query = query.filter(
            LegalNotificationPreference.organization_id == current_user.organization_id
        )
    return query.all()


@router.put("/notification-preferences/me")
def update_notification_preferences(
    event_type: str,
    body: NotificationPrefUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按事件类型更新通知偏好（未传字段保持原值，显式传 null 可清除静默/代理）。"""
    from app.services.notification.notification_service import EVENT_TYPES

    if event_type not in EVENT_TYPES:
        raise HTTPException(422, detail="通知事件类型不支持")
    if not current_user.organization_id:
        raise HTTPException(422, detail="用户未加入组织，无法保存通知偏好")

    pref = db.query(LegalNotificationPreference).filter(
        LegalNotificationPreference.user_id == current_user.id,
        LegalNotificationPreference.organization_id == current_user.organization_id,
        LegalNotificationPreference.event_type == event_type,
    ).first()

    if not pref:
        pref = LegalNotificationPreference(
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            event_type=event_type,
        )
        db.add(pref)

    data = body.model_dump(exclude_unset=True)
    if data.get("channels_json") is not None:
        try:
            channels = json.loads(data["channels_json"])
        except (TypeError, ValueError):
            raise HTTPException(422, detail="channels_json 必须是 JSON 数组")
        if not isinstance(channels, list) or any(not isinstance(item, str) for item in channels):
            raise HTTPException(422, detail="channels_json 必须是 JSON 数组")
        unknown = set(channels) - ALLOWED_CHANNELS
        if unknown:
            raise HTTPException(422, detail=f"不支持的通知渠道: {', '.join(sorted(unknown))}")
        pref.channels_json = json.dumps(list(dict.fromkeys(channels)), ensure_ascii=False)

    if "mute_start" in data:
        pref.mute_start = _normalize_mute(data["mute_start"], "mute_start")
    if "mute_end" in data:
        pref.mute_end = _normalize_mute(data["mute_end"], "mute_end")

    if data.get("timezone"):
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(data["timezone"])
        except Exception:  # noqa: BLE001 - 非法时区统一按 422 返回
            raise HTTPException(422, detail="时区不合法")
        pref.timezone = data["timezone"]

    if "delegate_user_id" in data:
        delegate_id = data["delegate_user_id"]
        if delegate_id is not None:
            delegate = db.get(User, delegate_id)
            if delegate is None or delegate.organization_id != current_user.organization_id:
                raise HTTPException(422, detail="代理接收人必须是同组织成员")
        pref.delegate_user_id = delegate_id

    if "summary_frequency" in data:
        frequency = data["summary_frequency"] or "none"
        if frequency not in SUMMARY_FREQUENCIES:
            raise HTTPException(422, detail="摘要频率不支持")
        pref.summary_frequency = frequency

    db.commit()
    db.refresh(pref)
    return pref


@router.put("/cases/{case_id}/notification-policy")
def update_notification_policy(
    case_id: int,
    event_type: str,
    advance_days_json: Optional[str] = None,
    escalation_user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证案件访问权限
    from app.models.legal import LegalCase
    case = db.query(LegalCase).filter(LegalCase.id == case_id).first()
    if not case:
        raise HTTPException(404, detail="案件不存在")

    # 案件策略影响客户与律师提醒，仅 reviewer/admin 可修改。
    from app.services.org.org_service import org_service
    member = org_service.get_user_org_member(
        db=db,
        user_id=current_user.id,
        org_id=case.organization_id
    )
    if not member:
        raise HTTPException(404, detail="案件不存在")
    if member.legal_role not in ("admin", "reviewer"):
        raise HTTPException(403, detail="仅审核律师或管理员可修改案件通知策略")

    if advance_days_json is not None:
        try:
            offsets = json.loads(advance_days_json)
        except (TypeError, ValueError):
            raise HTTPException(422, detail="advance_days_json 必须是正整数数组")
        if not isinstance(offsets, list) or any(not isinstance(day, int) or day < 0 or day > 365 for day in offsets):
            raise HTTPException(422, detail="advance_days_json 必须是 0-365 的整数数组")
    if escalation_user_id is not None:
        escalation_member = org_service.get_user_org_member(
            db=db, user_id=escalation_user_id, org_id=case.organization_id,
        )
        if not escalation_member:
            raise HTTPException(422, detail="升级接收人必须是案件所属组织成员")

    policy = db.query(LegalNotificationPolicy).filter(
        LegalNotificationPolicy.case_id == case_id,
        LegalNotificationPolicy.event_type == event_type,
    ).first()

    if not policy:
        policy = LegalNotificationPolicy(
            case_id=case_id,
            organization_id=case.organization_id,
            event_type=event_type,
        )
        db.add(policy)

    if advance_days_json is not None:
        policy.advance_days_json = advance_days_json
    if escalation_user_id is not None:
        policy.escalation_user_id = escalation_user_id
    policy.updated_by = current_user.id
    db.commit()
    db.refresh(policy)
    return policy


@router.post("/notifications/{notification_id}/acknowledge")
def acknowledge_notification(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notif = db.query(LegalNotificationEvent).filter(
        LegalNotificationEvent.id == notification_id,
        LegalNotificationEvent.user_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(404)
    from app.services.notification.notification_service import notification_service

    notification_service.mark_acknowledged(db, notif)
    db.commit()
    db.refresh(notif)
    return notif


@router.get("/notifications/me")
def get_my_notifications(
    status: str | None = Query(default=None, description="all / unread / read"),
    event_type: str | None = Query(default=None, max_length=64),
    case_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户站内通知，支持状态、案件、类型筛选和分页。"""
    from app.services.notification.notification_service import notification_service
    if status == "unread":
        status_filter = "unread"
    elif status in {None, "all"}:
        status_filter = None
    elif status == "read":
        status_filter = "read"
    else:
        raise HTTPException(status_code=422, detail="通知状态不支持")
    offset = (page - 1) * page_size
    events = notification_service.get_user_notifications(
        db=db, user_id=current_user.id, status=status_filter,
        event_type=event_type, case_id=case_id, limit=page_size, offset=offset,
    )
    total = notification_service.count_user_notifications(
        db=db, user_id=current_user.id,
        status=status_filter,
        event_type=event_type, case_id=case_id,
    )
    items = [notification_service.serialize_event(e) for e in events if e.status != "failed"]
    unread = notification_service.get_unread_count(db=db, user_id=current_user.id)
    return {"items": items, "unread": unread, "total": total, "page": page, "page_size": page_size}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """标记单条站内通知为已读；非本人或不存在返回 404。"""
    from app.services.notification.notification_service import notification_service
    try:
        notification_service.mark_as_read(db=db, event_id=notification_id, user_id=current_user.id)
    except ValueError:
        raise HTTPException(404, detail="通知不存在")
    return {"ok": True}


@router.post("/notifications/read-all")
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """标记当前用户全部站内通知为已读。"""
    from app.services.notification.notification_service import notification_service
    updated = notification_service.mark_all_as_read(db=db, user_id=current_user.id)
    return {"ok": True, "updated": updated}


class OnboardingUpdate(BaseModel):
    user_role: Optional[str] = None
    completed_steps_json: Optional[str] = None
    skipped_steps_json: Optional[str] = None


@router.get("/onboarding")
def get_onboarding(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.organization_id:
        raise HTTPException(400, detail="无所属组织")
    progress = db.query(OrganizationOnboardingProgress).filter(
        OrganizationOnboardingProgress.organization_id == current_user.organization_id
    ).first()
    return progress


@router.put("/onboarding")
def update_onboarding(
    body: OnboardingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.organization_id:
        raise HTTPException(400, detail="无所属组织")

    progress = db.query(OrganizationOnboardingProgress).filter(
        OrganizationOnboardingProgress.organization_id == current_user.organization_id
    ).first()

    if not progress:
        progress = OrganizationOnboardingProgress(
            organization_id=current_user.organization_id
        )
        db.add(progress)

    if body.user_role is not None:
        progress.user_role = body.user_role
    if body.completed_steps_json is not None:
        progress.completed_steps_json = body.completed_steps_json
    if body.skipped_steps_json is not None:
        progress.skipped_steps_json = body.skipped_steps_json

    db.commit()
    db.refresh(progress)
    return progress

