from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from app.core import database as database_module
from app.core.config import get_settings
from app.services.oplog_service import oplog_service


def structured_log_json(
    *,
    source: str,
    action: str,
    module: Optional[str] = None,
    actor: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    target_name: Optional[str] = None,
    detail: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> None:
    """等保差距 #2：SIEM 汇聚用结构化 JSON 行。

    STRUCTURED_LOG_JSON_LINES 开启时，把双轨日志（operation/audit/login）以单行
    JSON 输出到 audit.json logger，供集中日志/ELK 等采集；关闭时零开销。
    """
    if not get_settings().STRUCTURED_LOG_JSON_LINES:
        return
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "module": module,
        "action": action,
        "actor": actor,
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target_name,
        "detail": detail,
        "ip_address": ip_address,
    }
    logging.getLogger("audit.json").info(json.dumps(payload, ensure_ascii=False))


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
