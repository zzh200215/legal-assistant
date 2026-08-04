"""订阅与配额 API"""
from fastapi import APIRouter, Depends, Query, Request, Header
from sqlalchemy.orm import Session
from typing import Optional
import hashlib, hmac

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.api_response import api_error
from app.core.config import get_settings
from app.models.user import User
from app.models.subscription import SubscriptionPlan, UserSubscription, QuotaUsage
from app.services.subscription_service import subscription_service

router = APIRouter()
settings = get_settings()


# ================== 计划列表 ==================

@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    """获取所有订阅计划"""
    subscription_service.ensure_default_plans(db)
    plans = db.query(SubscriptionPlan).filter(SubscriptionPlan.is_active == True).all()
    return [_serialize_plan(p) for p in plans]


def _serialize_plan(plan: SubscriptionPlan) -> dict:
    return {
        "id": plan.id,
        "tier": plan.tier,
        "name": plan.name,
        "description": plan.description,
        "price_monthly": float(plan.price_monthly),
        "quota_consultation": plan.quota_consultation,
        "quota_review": plan.quota_review,
        "quota_draft": plan.quota_draft,
    }


# ================== 当前用户订阅 ==================

@router.get("/subscriptions/me")
def get_my_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户订阅状态"""
    subscription_service.ensure_default_plans(db)
    plan = subscription_service.get_user_plan(db, current_user.id)
    sub = subscription_service.get_active_subscription(db, current_user.id)

    return {
        "plan": _serialize_plan(plan) if plan else None,
        "subscription": _serialize_sub(sub) if sub else None,
    }


def _serialize_sub(sub: UserSubscription) -> dict:
    return {
        "id": sub.id,
        "plan_id": sub.plan_id,
        "status": sub.status,
        "payment_provider": sub.payment_provider,
        "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


# ================== 配额用量 ==================

@router.get("/subscriptions/quota")
def get_my_quota(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当月配额使用情况"""
    subscription_service.ensure_default_plans(db)
    return subscription_service.get_usage_summary(db, current_user.id)


# ================== 支付 Checkout ==================

@router.post("/subscriptions/checkout")
def create_checkout(
    tier: str = Query(..., description="pro / team"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建支付会话（返回跳转URL，实际支付由前端完成）"""
    valid_tiers = ("pro", "team")
    if tier not in valid_tiers:
        raise api_error(400, f"无效的计划类型，可选: {valid_tiers}", code="INVALID_TIER")

    from app.services.oplog_service import oplog_service  # noqa: E402

    oplog_service.log(
        module="subscription",
        action="upgrade_intent",
        db=db,
        user_id=current_user.id,
        target_type="subscription",
        target_id=None,
        detail=f"tier={tier}",
    )

    # 若配置了真实支付网关，使用配置的 base URL；否则提示未配置
    base_url = settings.PAYMENT_CHECKOUT_BASE_URL
    if not base_url:
        return {
            "checkout_url": None,
            "tier": tier,
            "message": "支付网关尚未配置，请联系管理员设置 PAYMENT_CHECKOUT_BASE_URL",
            "configured": False,
        }
    checkout_url = f"{base_url.rstrip('/')}/checkout?plan={tier}&user={current_user.id}"
    return {
        "checkout_url": checkout_url,
        "tier": tier,
        "message": "请跳转到支付页面完成订阅",
        "configured": True,
    }


# ================== 支付 Webhook ==================

@router.post("/subscriptions/webhook")
async def payment_webhook(
    request: Request,
    x_stripe_signature: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """
    支付回调（Stripe / Ping++）。
    生产环境需验证签名；此处接受 provider 字段判断来源。
    """
    try:
        payload = await request.json()
    except Exception:
        raise api_error(400, "无法解析 Webhook 载荷", code="INVALID_PAYLOAD")

    provider = payload.get("provider", "stripe")
    event_type = payload.get("event_type") or payload.get("type", "")

    if provider == "stripe":
        return _handle_stripe_event(db, event_type, payload)
    elif provider == "pingpp":
        return _handle_pingpp_event(db, event_type, payload)
    else:
        raise api_error(400, f"不支持的支付提供商: {provider}", code="UNSUPPORTED_PROVIDER")


def _handle_stripe_event(db: Session, event_type: str, payload: dict) -> dict:
    """处理 Stripe 事件"""
    if event_type in ("customer.subscription.created", "customer.subscription.updated"):
        data = payload.get("data", {}).get("object", {})
        _activate_from_webhook(
            db=db,
            user_id=int(data.get("metadata", {}).get("user_id", 0)),
            plan_tier=data.get("metadata", {}).get("plan_tier", "pro"),
            payment_provider="stripe",
            payment_subscription_id=data.get("id", ""),
            payment_customer_id=data.get("customer", ""),
        )
    elif event_type == "customer.subscription.deleted":
        sub_id = payload.get("data", {}).get("object", {}).get("id", "")
        _cancel_by_provider_id(db, sub_id)

    return {"received": True}


def _handle_pingpp_event(db: Session, event_type: str, payload: dict) -> dict:
    """处理 Ping++ 事件"""
    if event_type == "charge.succeeded":
        metadata = payload.get("data", {}).get("object", {}).get("metadata", {})
        _activate_from_webhook(
            db=db,
            user_id=int(metadata.get("user_id", 0)),
            plan_tier=metadata.get("plan_tier", "pro"),
            payment_provider="pingpp",
            payment_subscription_id=payload.get("data", {}).get("object", {}).get("id", ""),
        )
    return {"received": True}


def _activate_from_webhook(
    db: Session,
    user_id: int,
    plan_tier: str,
    payment_provider: str,
    payment_subscription_id: str,
    payment_customer_id: Optional[str] = None,
) -> None:
    if not user_id:
        return

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return

    subscription_service.ensure_default_plans(db)
    subscription_service.activate_subscription(
        db=db,
        user_id=user_id,
        plan_tier=plan_tier,
        payment_provider=payment_provider,
        payment_subscription_id=payment_subscription_id,
        payment_customer_id=payment_customer_id,
    )


def _cancel_by_provider_id(db: Session, subscription_id: str) -> None:
    from app.models.subscription import SubscriptionStatus
    from datetime import datetime, timezone
    sub = db.query(UserSubscription).filter(
        UserSubscription.payment_subscription_id == subscription_id
    ).first()
    if sub:
        sub.status = SubscriptionStatus.cancelled.value
        sub.cancelled_at = datetime.now(timezone.utc)
        db.add(sub)
        db.commit()


# ================== 取消订阅 ==================

@router.post("/subscriptions/cancel")
def cancel_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """取消当前订阅（降回免费版）"""
    sub = subscription_service.cancel_subscription(db, current_user.id)
    if not sub:
        raise api_error(404, "没有活跃订阅", code="NO_ACTIVE_SUBSCRIPTION")
    return {"message": "订阅已取消，将降回免费版"}
