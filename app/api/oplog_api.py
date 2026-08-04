from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.user import User
from app.services.oplog_service import oplog_service

router = APIRouter()


@router.get("/")
def list_logs(
    module: str | None = Query(None, description="按模块筛选: document/meeting/email/task/agent/chat/prompt"),
    days: int = Query(30, description="统计天数"),
    limit: int = Query(200, description="返回条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取操作日志列表"""
    logs = oplog_service.list_logs(db, user_id=current_user.id, module=module, days=days, limit=limit)
    return [
        {
            "id": log.id,
            "module": log.module,
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "created_at": log.created_at,
        }
        for log in logs
    ]


@router.get("/stats")
def my_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取用户操作统计"""
    return oplog_service.get_user_stats(current_user.id, db, days=days)


@router.post("/")
def create_log(
    request: Request,
    module: str,
    action: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    target_type: str | None = None,
    target_id: int | None = None,
    detail: str | None = None,
):
    """手动记录操作日志（也可由后端内部调用）"""
    ip = request.client.host if request.client else None
    log = oplog_service.log(
        module=module,
        action=action,
        db=db,
        user_id=current_user.id,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip_address=ip,
    )
    return {"id": log.id, "detail": "已记录"}
