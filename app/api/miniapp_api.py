"""微信小程序 API：登录 + 简化咨询入口"""
from typing import Optional

import requests
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import create_access_token
from app.core.api_response import api_error
from app.core.config import get_settings
from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.user import User, UserStatus, UserRole, WechatUser
from app.services.subscription_service import subscription_service

router = APIRouter()
settings = get_settings()


def _miniapp_code2session(js_code: str) -> dict:
    """调用微信小程序 code2Session 接口"""
    url = (
        f"https://api.weixin.qq.com/sns/jscode2session"
        f"?appid={settings.WECHAT_APP_ID}"
        f"&secret={settings.WECHAT_APP_SECRET}"
        f"&js_code={js_code}"
        f"&grant_type=authorization_code"
    )
    try:
        resp = requests.get(url, timeout=10)
        return resp.json()
    except Exception as e:
        raise api_error(500, f"微信登录服务不可用: {e}", code="WECHAT_SERVICE_UNAVAILABLE")


@router.post("/login")
def miniapp_login(
    js_code: str = Query(..., description="wx.login() 返回的 code"),
    nickname: Optional[str] = Query(None, max_length=128),
    avatar_url: Optional[str] = Query(None, max_length=512),
    db: Session = Depends(get_db),
):
    """
    微信小程序登录。
    前端调用 wx.login() 获取 code，传给此端点，返回 JWT。
    首次登录自动创建账号；已绑定则直接登录。
    """
    if not settings.WECHAT_APP_ID or not settings.WECHAT_APP_SECRET:
        raise api_error(500, "微信小程序未配置", code="WECHAT_NOT_CONFIGURED")

    session_data = _miniapp_code2session(js_code)

    if "errcode" in session_data and session_data["errcode"] != 0:
        raise api_error(401, f"微信登录失败: {session_data.get('errmsg', '')}", code="WECHAT_LOGIN_FAILED")

    openid = session_data.get("openid")
    unionid = session_data.get("unionid")

    if not openid:
        raise api_error(401, "无法获取微信 openid", code="WECHAT_LOGIN_FAILED")

    # 查找已绑定用户
    wx_user = db.query(WechatUser).filter(WechatUser.openid == openid).first()

    if wx_user:
        user = db.query(User).filter(User.id == wx_user.user_id).first()
        if not user or user.status != UserStatus.active.value:
            raise api_error(403, "账号已被禁用", code="ACCOUNT_DISABLED")

        # 更新 nickname/avatar（若提供）
        if nickname and wx_user.nickname != nickname:
            wx_user.nickname = nickname[:128]
            db.add(wx_user)
        if avatar_url and wx_user.avatar_url != avatar_url:
            wx_user.avatar_url = avatar_url[:512]
            db.add(wx_user)
        db.commit()
    else:
        # 首次登录，创建账号
        username = f"mp_{openid[:16]}"
        email = f"{openid}@miniapp.placeholder"

        user = User(
            username=username,
            email=email,
            full_name=(nickname[:128] if nickname else username),
            role=UserRole.user.value,
            status=UserStatus.active.value,
        )
        db.add(user)
        db.flush()

        wx_user = WechatUser(
            user_id=user.id,
            openid=openid,
            unionid=unionid,
            nickname=nickname[:128] if nickname else None,
            avatar_url=avatar_url[:512] if avatar_url else None,
        )
        db.add(wx_user)
        db.commit()
        db.refresh(user)

    token = create_access_token({"sub": user.id, "role": user.role})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user_id": user.id,
        "nickname": wx_user.nickname,
        "is_new": wx_user.id is not None and user.id is not None,
    }


@router.post("/consultations")
async def miniapp_consultation(
    question: str = Query(..., min_length=1, max_length=2000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    小程序简化版咨询入口：单问题，返回精简响应。
    复用法律咨询业务逻辑，配额共享。
    """
    subscription_service.ensure_default_plans(db)
    if not subscription_service.check_quota(db, current_user.id, "consultation"):
        raise api_error(429, "本月咨询配额已用完，请升级订阅", code="QUOTA_EXCEEDED")

    # 延迟导入避免循环依赖
    from app.services.legal_service import consultation_payload, ensure_demo_sources
    from app.models.legal import LegalConsultation, LegalSource
    import json

    ensure_demo_sources(db, current_user.id)
    sources = db.query(LegalSource).filter(
        LegalSource.user_id == current_user.id,
        LegalSource.status == "active",
    ).all()

    category, known, missing, refs, advice, risk, status = await consultation_payload(
        question, sources, user_id=current_user.id, db=db
    )

    row = LegalConsultation(
        user_id=current_user.id,
        question=question,
        category=category,
        known_facts_json=json.dumps(known, ensure_ascii=False),
        missing_facts_json=json.dumps(missing, ensure_ascii=False),
        references_json=json.dumps(refs, ensure_ascii=False),
        advice=advice,
        risk_level=risk,
        status=status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    subscription_service.record_usage(db, current_user.id, "consultation")

    # 小程序返回精简字段
    return {
        "id": row.id,
        "advice": advice,
        "risk_level": risk,
        "category": category,
        "missing_facts": missing,
    }


@router.post("/contract-review")
async def miniapp_contract_review(
    title: str = Query(default="小程序合同", max_length=256),
    content: str = Query(..., min_length=10, max_length=20000, description="合同文本内容"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    小程序合同审查入口：提交合同文本，返回风险摘要。
    配额与主站共享。
    """
    subscription_service.ensure_default_plans(db)
    if not subscription_service.check_quota(db, current_user.id, "review"):
        raise api_error(429, "本月合同审查配额已用完，请升级订阅", code="QUOTA_EXCEEDED")

    from app.services.legal_service import review_contract
    from app.models.legal import ContractReview
    import json

    risks, summary = await review_contract(content, user_id=current_user.id)
    row = ContractReview(
        user_id=current_user.id,
        title=title.strip(),
        content=content,
        summary=summary,
        risks_json=json.dumps(risks, ensure_ascii=False),
        references_json="[]",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    subscription_service.record_usage(db, current_user.id, "review")

    return {
        "id": row.id,
        "summary": summary,
        "risk_count": len(risks),
        "high_risk_count": sum(1 for r in risks if r.get("risk_level") == "high"),
        "risks": risks[:5],  # 小程序精简返回前5条风险
    }


@router.get("/quota")
def miniapp_quota(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """小程序查询当前配额（供 UI 展示剩余次数）"""
    subscription_service.ensure_default_plans(db)
    return subscription_service.get_usage_summary(db, current_user.id)
