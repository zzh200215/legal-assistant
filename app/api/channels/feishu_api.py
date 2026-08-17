"""#87/飞书 M1 前置 API：open_id <-> user_id 绑定管理

回调安全复用统一 webhook HMAC 验签（app/core/webhook_verifier.py，P1-C）：
- url_verification 握手为飞书平台明文 challenge，不经签名（回调配置流程）；
- 事件回调要求有效签名（fail-closed：未配置密钥即拒绝并审计，不再静默放行）；
- v2 模式校验时间戳新鲜度窗口 + nonce 去重（DB 共享存储，多实例有效）；
- 验签/去重失败记录安全审计（不含密钥与完整载荷）。
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.core.auth import get_current_user, require_admin_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.webhook_audit import write_webhook_audit
from app.core.webhook_dedup import claim_nonce
from app.core.webhook_verifier import WebhookVerificationError, WebhookVerifier
from app.models.feishu_binding import FeishuBinding
from app.models.user import User

router = APIRouter()
settings = get_settings()


class BindRequest(BaseModel):
    open_id: str = Field(..., min_length=8, max_length=128)
    union_id: Optional[str] = Field(None, max_length=128)
    app_id: str = Field(..., min_length=4, max_length=64)


def _feishu_verifier(mode: str, secret: str) -> WebhookVerifier:
    """按 FEISHU_CALLBACK_VERIFY 构建统一验签器（auto 兼容 v2/v1/旧 hex）。"""
    scheme = {"v2": "feishu_v2", "v1": "feishu_v1"}.get(mode, "feishu_auto")
    return WebhookVerifier(secret, scheme=scheme, encoding="base64")


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
    - url_verification 握手：平台明文 challenge（不经签名）直接回显；
    - 事件回调验签 fail-closed（未配置密钥即拒绝）+ v2 时间窗 + nonce 去重；
    - im.message.receive_v1：ack 后转后台处理（咨询卡片回复），不阻塞回调。
    """
    from app.services.integration import feishu_service

    raw = await request.body()
    # 1) url_verification 握手：飞书平台配置回调时发送明文 challenge，无签名，
    #    不属事件回调，直接处理（避免把握手误判为未验签攻击）。
    try:
        probe = json.loads(raw)
    except ValueError:
        probe = None
    if isinstance(probe, dict) and probe.get("type") == "url_verification":
        payload = feishu_service.parse_event_body(raw, settings.FEISHU_EVENT_ENCRYPT_KEY)
        return feishu_service.handle_event(payload)

    mode = (settings.FEISHU_CALLBACK_VERIFY or "auto").lower()
    secret = settings.FEISHU_EVENT_ENCRYPT_KEY or ""
    if mode == "off":
        # 显式降级：跳过验签（仅排查/开发）。每次请求记录审计；生产不得使用。
        write_webhook_audit(
            db=db, result="degraded", provider="feishu",
            reason_code="WEBHOOK_VERIFICATION_DISABLED",
            sanitized_metadata=json.dumps({"mode": "off"}, ensure_ascii=False),
        )
    else:
        try:
            _feishu_verifier(mode, secret).verify(
                raw, x_lark_signature,
                timestamp=x_lark_request_timestamp, nonce=x_lark_request_nonce,
            )
            if x_lark_request_nonce:
                if not claim_nonce(
                    db, namespace="feishu", nonce=x_lark_request_nonce,
                    ttl_seconds=get_settings().WEBHOOK_REPLAY_TTL_SECONDS,
                ):
                    raise WebhookVerificationError("REPLAY", "飞书回调 nonce 重复（重放）")
        except WebhookVerificationError as exc:
            write_webhook_audit(
                db=db, result="blocked", provider="feishu",
                reason_code=exc.code,
                sanitized_metadata=json.dumps({"mode": mode}, ensure_ascii=False),
            )
            raise api_error(400, "飞书回调验签失败", code="INVALID_FEISHU_SIGNATURE")

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
