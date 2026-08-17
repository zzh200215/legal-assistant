"""开发者应用子路由：应用创建/列表、API Key 与 Webhook Secret 轮换、Webhook 测试、用量查询。"""
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, verify_org_role_access
from app.core.database import get_db
from app.models.legal_platform import DeveloperApiKey, DeveloperApiUsage, DeveloperApp, WebhookDelivery
from app.models.org import LegalMemberRole
from app.models.user import User

router = APIRouter()

class AppCreate(BaseModel):
    name: str = Field(..., max_length=128)
    ip_whitelist_json: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = Field(default=None, min_length=16, max_length=256)


def _gen_open_api_key() -> tuple[str, str, str]:
    raw = "lzj_op_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    # 列宽 String(12)，与 api_key_api 一致；超过 12 会触发 MySQL 1406
    key_prefix = raw[:12]
    return raw, key_hash, key_prefix


def _public_developer_app(app: DeveloperApp) -> dict:
    """Serialize developer apps without exposing webhook signing material."""
    return {
        "id": app.id,
        "organization_id": app.organization_id,
        "name": app.name,
        "status": app.status,
        "ip_whitelist_json": app.ip_whitelist_json,
        "webhook_url": app.webhook_url,
        "webhook_secret_configured": bool(
            app.webhook_secret_hash or app.webhook_secret_ciphertext
        ),
        "subscribed_events_json": app.subscribed_events_json,
        "created_by": app.created_by,
        "created_at": app.created_at,
        "updated_at": app.updated_at,
    }


@router.post("/orgs/{org_id}/apps")
def create_developer_app(
    org_id: int,
    body: AppCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from app.services.org.security_audit_service import write_event

    existing = db.query(DeveloperApp).filter(
        DeveloperApp.organization_id == org_id,
        DeveloperApp.name == body.name,
    ).first()
    if existing:
        raise HTTPException(409, detail="应用名称在本组织内已存在")

    app = DeveloperApp(
        organization_id=org_id,
        name=body.name,
        ip_whitelist_json=body.ip_whitelist_json,
        webhook_url=body.webhook_url,
        webhook_secret_hash=(hashlib.sha256(body.webhook_secret.encode()).hexdigest()
                             if body.webhook_secret else None),
        webhook_secret_ciphertext=body.webhook_secret,
        created_by=member.user_id,
    )
    db.add(app)
    db.flush()

    raw_key, key_hash, key_prefix = _gen_open_api_key()
    api_key = DeveloperApiKey(
        app_id=app.id,
        organization_id=org_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
    )
    db.add(api_key)
    db.commit()
    db.refresh(app)

    write_event(
        event_type="key_op", actor_type="user", actor_id=str(member.user_id),
        result="success", organization_id=org_id,
        target_type="developer_app", target_id=str(app.id), db=db,
    )
    return {
        "app": _public_developer_app(app),
        "api_key": raw_key,
        # The webhook secret is shown only in this creation response. It is never
        # returned by list/read endpoints or stored as plaintext.
        "webhook_secret": body.webhook_secret,
        "key_prefix": key_prefix,
    }


@router.get("/orgs/{org_id}/apps")
def list_developer_apps(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    apps = db.query(DeveloperApp).filter(DeveloperApp.organization_id == org_id).all()
    return [_public_developer_app(app) for app in apps]


@router.post("/orgs/{org_id}/apps/{app_id}/keys/rotate")
def rotate_api_key(
    org_id: int,
    app_id: int,
    transition_hours: int = 24,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    member = verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    from app.services.org.security_audit_service import write_event
    app = db.query(DeveloperApp).filter(
        DeveloperApp.id == app_id,
        DeveloperApp.organization_id == org_id
    ).first()
    if not app:
        raise HTTPException(404)

    from datetime import timedelta
    now = datetime.now(timezone.utc)

    old_keys = db.query(DeveloperApiKey).filter(
        DeveloperApiKey.app_id == app_id,
        DeveloperApiKey.status == "active",
    ).all()
    for old_key in old_keys:
        old_key.transition_until = now + timedelta(hours=transition_hours)

    raw_key, key_hash, key_prefix = _gen_open_api_key()
    new_key = DeveloperApiKey(
        app_id=app_id,
        organization_id=app.organization_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
    )
    db.add(new_key)
    db.commit()

    write_event(
        event_type="key_op", actor_type="user", actor_id=str(member.user_id),
        result="success", organization_id=org_id,
        target_type="developer_api_key", target_id=f"app:{app_id}",
        db=db,
    )
    return {"new_api_key": raw_key, "key_prefix": key_prefix, "transition_hours": transition_hours}


@router.post("/orgs/{org_id}/apps/{app_id}/webhook-secret/rotate")
def rotate_webhook_secret(
    org_id: int,
    app_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a new delivery-signing secret and reveal it exactly once."""
    member = verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    app = db.query(DeveloperApp).filter(
        DeveloperApp.id == app_id,
        DeveloperApp.organization_id == org_id,
    ).first()
    if app is None:
        raise HTTPException(404, detail="应用不存在")

    raw_secret = secrets.token_urlsafe(32)
    app.webhook_secret_hash = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()
    app.webhook_secret_ciphertext = raw_secret
    db.commit()

    from app.services.org.security_audit_service import write_event
    write_event(
        event_type="key_op", actor_type="user", actor_id=str(member.user_id),
        result="success", organization_id=org_id,
        target_type="developer_app", target_id=f"app:{app_id}:webhook_secret", db=db,
    )
    return {"webhook_secret": raw_secret}


@router.post("/orgs/{org_id}/apps/{app_id}/webhooks/test")
def test_webhook(
    org_id: int,
    app_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    app = db.query(DeveloperApp).filter(
        DeveloperApp.id == app_id,
        DeveloperApp.organization_id == org_id
    ).first()
    if not app or not app.webhook_url:
        raise HTTPException(400, detail="应用不存在或未配置 Webhook 地址")

    delivery = WebhookDelivery(
        subscription_id=None,
        app_id=app_id,
        organization_id=org_id,
        event_type="test.ping",
        event_id=f"test_{secrets.token_hex(8)}",
        status="pending",
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)
    return {"delivery_id": delivery.id, "status": "queued", "webhook_url": app.webhook_url}


@router.get("/orgs/{org_id}/apps/{app_id}/usage")
def get_api_usage(
    org_id: int,
    app_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.admin, db)
    # 验证app归属
    app = db.query(DeveloperApp).filter(
        DeveloperApp.id == app_id,
        DeveloperApp.organization_id == org_id
    ).first()
    if not app:
        raise HTTPException(404, detail="应用不存在")

    q = db.query(DeveloperApiUsage).filter(DeveloperApiUsage.app_id == app_id)
    if start_date:
        q = q.filter(DeveloperApiUsage.stat_date >= start_date)
    if end_date:
        q = q.filter(DeveloperApiUsage.stat_date <= end_date)
    total = q.count()
    items = q.order_by(DeveloperApiUsage.stat_date.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}

