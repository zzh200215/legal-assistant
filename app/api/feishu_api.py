"""#87/飞书 M1 前置 API：open_id <-> user_id 绑定管理

回调安全复用 webhook HMAC 模式（tasks/__init__.py 同款），开发立项时接入飞书回调。
"""

import base64
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


def _verify_callback_signature(
    raw_body: bytes,
    signature: str | None,
    timestamp: Optional[str] = None,
    nonce: Optional[str] = None,
) -> bool:
    """飞书事件回调验签（指南 §6）。

    FEISHU_CALLBACK_VERIFY 取值：
    - auto（默认）：按 V2 → V1 → 旧 hex 顺序尝试，任中即通过；
    - v2：仅 base64(HmacSHA256(timestamp+nonce+encrypt_key+raw_body, encrypt_key))；
    - v1：仅 base64(HmacSHA256(raw_body, encrypt_key))；
    - off：跳过验签（临时排查用）。
    """
    secret = settings.FEISHU_EVENT_ENCRYPT_KEY
    mode = (settings.FEISHU_CALLBACK_VERIFY or "auto").lower()
    if not secret or mode == "off":
        return True  # 未配置密钥时跳过（开发/测试）；off 为临时排查
    if not signature:
        return False
    key = secret.encode("utf-8")
    if mode in {"v2", "auto"} and timestamp is not None and nonce is not None:
        v2_input = f"{timestamp}{nonce}{secret}".encode("utf-8") + raw_body
        v2_sig = base64.b64encode(hmac.new(key, v2_input, hashlib.sha256).digest()).decode("utf-8")
        if hmac.compare_digest(v2_sig, signature):
            return True
    if mode in {"v1", "auto"}:
        v1_sig = base64.b64encode(hmac.new(key, raw_body, hashlib.sha256).digest()).decode("utf-8")
        if hmac.compare_digest(v1_sig, signature):
            return True
    if mode == "auto":
        # 兼容早期简化实现（hex），新接入环境勿依赖
        hex_sig = hmac.new(key, raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(hex_sig, signature):
            return True
    return False


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
    x_lark_request_timestamp: Optional[str] = Header(None),
    x_lark_request_nonce: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """飞书事件回调（M1 咨询卡片等）：验签 + 解密 + 分派。

    - encrypt_key 模式：body 为 {"encrypt": ...}，AES-256-CBC 解密后分派；
    - url_verification 握手：回显 challenge；
    - im.message.receive_v1：ack 后转后台处理（咨询卡片回复），不阻塞回调。
    """
    from app.services import feishu_service

    raw = await request.body()
    if not _verify_callback_signature(
        raw, x_lark_signature, timestamp=x_lark_request_timestamp, nonce=x_lark_request_nonce
    ):
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
