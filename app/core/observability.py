from __future__ import annotations

from app.core import database as database_module
from app.services.oplog_service import oplog_service


def log_async_task_event(
    *,
    user_id: int | None,
    module: str,
    action: str,
    target_type: str,
    target_id: int | None,
    detail: str,
) -> None:
    db = database_module.SessionLocal()
    try:
        oplog_service.log(
            module=module,
            action=action,
            db=db,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    finally:
        db.close()
