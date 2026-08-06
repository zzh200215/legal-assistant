from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from app.core import database as database_module
from app.core.config import get_settings
from app.services.oplog_service import oplog_service

_audit_handler_configured = False


def _ensure_audit_file_handler() -> None:
    """把 audit.json logger 接到落盘 FileHandler（幂等，供 SIEM/集中日志采集）。

    仅在 STRUCTURED_LOG_JSON_LINES 开启时由 structured_log_json 调用一次；
    目录不存在自动创建；重复调用不重复添加 handler。
    """
    global _audit_handler_configured
    if _audit_handler_configured:
        return
    logger = logging.getLogger("audit.json")
    if not logger.handlers:
        try:
            path = get_settings().STRUCTURED_LOG_FILE.strip()
            if path:
                directory = os.path.dirname(os.path.abspath(path))
                if directory:
                    os.makedirs(directory, exist_ok=True)
                handler = logging.FileHandler(path, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(message)s"))
                logger.addHandler(handler)
                logger.setLevel(logging.INFO)
                logger.propagate = False
        except OSError as exc:
            logging.getLogger(__name__).warning("audit.json 落盘失败，结构化日志仅输出到 root: %s", exc)
    _audit_handler_configured = True


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
    JSON 输出到 audit.json 日志（自动落盘 STRUCTURED_LOG_FILE，供集中日志/ELK 采集）；
    关闭时零开销。
    """
    if not get_settings().STRUCTURED_LOG_JSON_LINES:
        return
    _ensure_audit_file_handler()
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
