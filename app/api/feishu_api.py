"""#87/飞书 M1 前置 API：open_id <-> user_id 绑定管理

回调安全复用 webhook HMAC 模式（tasks/__init__.py 同款），开发立项时接入飞书回调。
"""

import hashlib
import hmac
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.core.auth import get_current_user, require_admin_user
from app.core.config import get_settings
from app.core.database import get_db
from app.models.feishu_binding import FeishuBinding
from app.models.user import User

router = APIRouter()
settings = get_settings()


class BindRequest(BaseModel):
    open_id: str = Field(..., min_length=8, max_length=128)
    union_id: Optional[str] = Field(None, max_length=128)
    app_id: str = Field(..., min_length=4, max_length=64)


def _verify_callback_signature(raw_body: bytes, signature: str | None) -> bool:
    """飞书事件回调验签：X-Lark-Signature = HMAC-SHA256(encrypt_key, raw_body)"""
    secret = settings.FEISHU_EVENT_ENCRYPT_KEY
    if not secret:
        return True  # 未配置密钥时跳过（开发/测试）
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/bindings")
def bind_feishu(
    req: BindRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户绑定飞书 open_id（企业自建应用扫码绑定后回调调用）"""
    existing = db.query(FeishuBinding).filter(
        (FeishuBinding.user_id == current_user.id) | (FeishuBinding.open_id == req.open_id)
    ).first()
    if existing:
        if existing.user_id == current_user.id and existing.open_id == req.open_id:
            return {"binding_id": existing.id, "already_bound": True}
        raise api_error(409, "open_id 或用户已被其他绑定占用", code="BINDING_CONFLICT")
    binding = FeishuBinding(
        user_id=current_user.id,
        open_id=req.open_id,
        union_id=req.union_id,
        app_id=req.app_id,
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return {"binding_id": binding.id}


@router.delete("/bindings/me")
def unbind_feishu(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    binding = db.query(FeishuBinding).filter(FeishuBinding.user_id == current_user.id).first()
    if binding:
        binding.status = "revoked"
        db.add(binding)
        db.commit()
    return {"revoked": binding is not None}


@router.get("/bindings/me")
def my_binding(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    binding = db.query(FeishuBinding).filter(
        FeishuBinding.user_id == current_user.id,
        FeishuBinding.status == "active",
    ).first()
    if not binding:
        return {"bound": False}
    return {"bound": True, "open_id": binding.open_id, "app_id": binding.app_id}


@router.post("/callbacks/event")
async def feishu_event_callback(
    request: Request,
    x_lark_signature: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """飞书事件回调（M1 咨询卡片等）：验签 + 解密 + 分派。

    - encrypt_key 模式：body 为 {"encrypt": ...}，AES-256-CBC 解密后分派；
    - url_verification 握手：回显 challenge；
    - im.message.receive_v1：ack 后转后台处理（咨询卡片回复），不阻塞回调。
    """
    from app.services import feishu_service

    raw = await request.body()
    if not _verify_callback_signature(raw, x_lark_signature):
        raise api_error(400, "飞书回调签名无效", code="INVALID_FEISHU_SIGNATURE")
    try:
        payload = feishu_service.parse_event_body(raw, settings.FEISHU_EVENT_ENCRYPT_KEY)
    except Exception:
        raise api_error(400, "无法解析/解密飞书回调载荷", code="INVALID_PAYLOAD")
    return feishu_service.handle_event(payload)


@router.get("/admin/bindings")
def list_bindings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    rows = db.query(FeishuBinding).order_by(FeishuBinding.created_at.desc()).limit(100).all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "open_id": r.open_id,
            "app_id": r.app_id,
            "status": r.status,
            "created_at": str(r.created_at),
        }
        for r in rows
    ]
