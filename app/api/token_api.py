from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.services.token_service import token_service

router = APIRouter()


@router.get("/my-stats")
def my_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户的 Token 使用统计"""
    return token_service.get_user_stats(current_user.id, db, days=days)


@router.get("/global-stats")
def global_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取全局 Token 使用统计"""
    return token_service.get_global_stats(db, days=days)
