"""通用幂等键服务：DB 唯一约束作为并发最终保障，Redis/内存仅为可选加速。

语义（scope + idempotency_key）：
- 首次请求：登记 in_progress，执行业务，成功后写入 completed + response_snapshot。
- 同 key + 同 request_hash 重放：直接返回缓存快照（幂等重试）。
- 同 key + 不同 request_hash：抛 IdempotencyConflictError（409）。
- 同 key 并发（已有 in_progress / 唯一约束冲突）：抛 IdempotencyConflictError（409）。
- 业务失败（failed）：允许同 key 重试。
- 过期（expires_at）：清理任务删除后可复用。

幂等不破坏已有 API 行为：本服务是增量接入，未接入的端点不受影响。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.error_codes import IDEMPOTENCY_KEY_CONFLICT, IDEMPOTENCY_KEY_IN_PROGRESS
from app.models.idempotency import IdempotencyKey


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class IdempotencyConflictError(Exception):
    """幂等冲突：同 key 不同请求 / 请求正在处理中 / 并发占用。API 层映射 409。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class IdempotencyService:
    def begin(self, db: Session, *, scope: str, key: str, request_hash: str,
              endpoint: str | None = None, user_id: int | None = None,
              organization_id: int | None = None, resource_id: str | int | None = None) -> dict:
        """注册幂等请求。返回 ``{"replay": bool, "response_snapshot": str|None}``。

        冲突时抛 IdempotencyConflictError；replay=True 时调用方应直接返回快照。
        endpoint/user_id/organization_id/resource_id 为可审计扩展列（服务端解析）。
        """
        ttl = get_settings().IDEMPOTENCY_KEY_TTL_SECONDS
        now = _utcnow()
        row = self._get(db, scope, key)
        if row is not None and row.expires_at is not None and row.expires_at < now:
            db.delete(row)
            db.commit()
            row = None
        if row is not None:
            if row.status == "completed":
                if row.request_hash != request_hash:
                    raise IdempotencyConflictError(
                        IDEMPOTENCY_KEY_CONFLICT, "幂等键已用于不同请求载荷")
                return {"replay": True, "response_snapshot": row.response_snapshot}
            if row.status == "in_progress":
                raise IdempotencyConflictError(
                    IDEMPOTENCY_KEY_IN_PROGRESS, "该幂等键请求正在处理中，请稍后重试")
            # failed：业务失败，允许同 key 重试
            row.status = "in_progress"
            row.request_hash = request_hash
            row.expires_at = now + timedelta(seconds=ttl)
            if endpoint is not None:
                row.endpoint = endpoint
            if user_id is not None:
                row.user_id = user_id
            if organization_id is not None:
                row.organization_id = organization_id
            if resource_id is not None:
                row.resource_id = str(resource_id)
            db.commit()
            return {"replay": False, "response_snapshot": None}

        db.add(IdempotencyKey(
            scope=scope,
            idempotency_key=key,
            request_hash=request_hash,
            status="in_progress",
            endpoint=endpoint,
            user_id=user_id,
            organization_id=organization_id,
            resource_id=str(resource_id) if resource_id is not None else None,
            expires_at=now + timedelta(seconds=ttl),
        ))
        try:
            db.commit()
        except IntegrityError:
            # 并发同 key 同时 INSERT：唯一约束兜底，取其中一个为冲突方
            db.rollback()
            raise IdempotencyConflictError(IDEMPOTENCY_KEY_CONFLICT, "并发请求已占用该幂等键")
        return {"replay": False, "response_snapshot": None}

    def complete(self, db: Session, *, scope: str, key: str, response_snapshot: Any,
                 resource_id: str | int | None = None) -> None:
        row = self._get(db, scope, key)
        if row is None:
            return
        row.status = "completed"
        row.response_snapshot = json.dumps(response_snapshot, ensure_ascii=False, default=str)
        if resource_id is not None:
            row.resource_id = str(resource_id)
        db.commit()

    def fail(self, db: Session, *, scope: str, key: str) -> None:
        row = self._get(db, scope, key)
        if row is None:
            return
        row.status = "failed"
        db.commit()

    def cleanup_expired(self, db: Session, *, batch_size: int | None = None) -> int:
        """批量删除过期幂等键，返回删除数。可安全重复调用。"""
        batch = batch_size or get_settings().DATABASE_ARCHIVE_BATCH_SIZE
        now = _utcnow()
        total = 0
        while True:
            ids = [
                row[0]
                for row in db.query(IdempotencyKey.id)
                .filter(IdempotencyKey.expires_at < now)
                .limit(batch)
                .all()
            ]
            if not ids:
                break
            db.query(IdempotencyKey).filter(IdempotencyKey.id.in_(ids)).delete(synchronize_session=False)
            db.commit()
            total += len(ids)
        return total

    @staticmethod
    def _get(db: Session, scope: str, key: str) -> IdempotencyKey | None:
        return (
            db.query(IdempotencyKey)
            .filter(
                IdempotencyKey.scope == scope,
                IdempotencyKey.idempotency_key == key,
            )
            .first()
        )


idempotency_service = IdempotencyService()
