from datetime import timedelta
from sqlalchemy.orm import Session

from app.models.operation_log import OperationLog
from app.core.time import utc_now


class OperationLogService:
    def log(
        self,
        module: str,
        action: str,
        db: Session,
        user_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        detail: str | None = None,
        ip_address: str | None = None,
    ) -> OperationLog:
        # P1：落库前统一脱敏（detail 只保留截断摘要，正文/密钥/PII 不落库），
        # 与 structured_log_json 的 JSON 行共用同一脱敏层。
        from app.core.observability_sanitizer import redact_payload

        safe_detail = redact_payload(detail) if detail else None
        entry = OperationLog(
            user_id=user_id,
            module=module,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=safe_detail,
            ip_address=ip_address,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        # 等保差距 #2：STRUCTURED_LOG_JSON_LINES 开启时同步输出 JSON 行（懒导入避免循环）
        from app.core.observability import structured_log_json

        structured_log_json(
            source="operation_log", module=module, action=action,
            actor=str(user_id) if user_id else None,
            target_type=target_type, target_id=target_id, detail=safe_detail, ip_address=ip_address,
        )
        return entry

    def list_logs(
        self,
        db: Session,
        user_id: int | None = None,
        module: str | None = None,
        days: int = 30,
        limit: int = 200,
    ) -> list[OperationLog]:
        since = utc_now() - timedelta(days=days)
        query = db.query(OperationLog).filter(OperationLog.created_at >= since)
        if user_id:
            query = query.filter(OperationLog.user_id == user_id)
        if module:
            query = query.filter(OperationLog.module == module)
        return query.order_by(OperationLog.created_at.desc()).limit(limit).all()

    def get_user_stats(self, user_id: int, db: Session, days: int = 30) -> dict:
        since = utc_now() - timedelta(days=days)
        rows = db.query(OperationLog).filter(
            OperationLog.user_id == user_id,
            OperationLog.created_at >= since,
        ).all()

        by_module = {}
        for r in rows:
            key = r.module
            if key not in by_module:
                by_module[key] = 0
            by_module[key] += 1

        return {
            "total_operations": len(rows),
            "by_module": by_module,
        }


oplog_service = OperationLogService()
