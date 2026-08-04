"""开放 API Key 管理 API — Phase 11 基础"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.api_key import APIKey, generate_api_key, hash_api_key
from app.models.user import User

router = APIRouter()


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    expires_days: Optional[int] = Field(default=None, ge=1, le=3650)


def _serialize(k: APIKey) -> dict:
    return {
        "id": k.id,
        "name": k.name,
        "key_prefix": k.key_prefix,
        "is_active": k.is_active,
        "created_at": k.created_at,
        "last_used_at": k.last_used_at,
        "expires_at": k.expires_at,
    }


@router.get("/api-keys")
def list_api_keys(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出当前用户所有有效 API Key（不返回明文 key）。"""
    keys = (
        db.query(APIKey)
        .filter(APIKey.user_id == current_user.id, APIKey.is_active == True)
        .order_by(APIKey.created_at.desc())
        .all()
    )
    return [_serialize(k) for k in keys]


@router.post("/api-keys", status_code=201)
def create_api_key(
    req: APIKeyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建新 API Key。明文 key 仅在创建时返回一次，请妥善保存。"""
    active_count = (
        db.query(APIKey)
        .filter(APIKey.user_id == current_user.id, APIKey.is_active == True)
        .count()
    )
    if active_count >= 10:
        raise api_error(400, "最多同时持有 10 个 API Key", code="API_KEY_LIMIT_REACHED")

    raw_key = generate_api_key()
    expires_at = None
    if req.expires_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=req.expires_days)

    key = APIKey(
        user_id=current_user.id,
        name=req.name,
        key_hash=hash_api_key(raw_key),
        key_prefix=raw_key[:12],
        expires_at=expires_at,
    )
    db.add(key)
    db.commit()
    db.refresh(key)

    return {
        **_serialize(key),
        "key": raw_key,
        "note": "此为唯一一次展示完整 Key，请妥善保存。",
    }


@router.delete("/api-keys/{key_id}")
def revoke_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销指定 API Key。"""
    key = (
        db.query(APIKey)
        .filter(APIKey.id == key_id, APIKey.user_id == current_user.id)
        .first()
    )
    if not key:
        raise api_error(404, "API Key 不存在", code="API_KEY_NOT_FOUND")
    key.is_active = False
    db.commit()
    return {"id": key_id, "message": "API Key 已撤销"}


@router.get("/api-keys/{key_id}")
def get_api_key(
    key_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个 API Key 详情（不含明文 key）。"""
    key = (
        db.query(APIKey)
        .filter(APIKey.id == key_id, APIKey.user_id == current_user.id)
        .first()
    )
    if not key:
        raise api_error(404, "API Key 不存在", code="API_KEY_NOT_FOUND")
    return _serialize(key)
