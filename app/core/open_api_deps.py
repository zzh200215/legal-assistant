"""API 调用量追踪 + 幂等键 dependency — PRD V3.0 § 9.4.1 / 9.8.2"""

import hashlib
import time
from datetime import date, datetime, timezone
from typing import Optional

import redis as redis_lib
from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db


# ── P13-3: API 调用量追踪 ─────────────────────────────────────────────────────

def track_api_usage(
    *,
    app_id: int,
    org_id: int,
    endpoint: str,
    method: str,
    status_code: int,
    duration_ms: int,
    db: Session,
) -> None:
    """将一次 Open API 调用写入 developer_api_usage（聚合到小时粒度）。失败静默。"""
    try:
        from app.models.legal_platform import DeveloperApiUsage
        today = date.today().isoformat()
        hour = datetime.now(timezone.utc).hour

        existing = db.query(DeveloperApiUsage).filter(
            DeveloperApiUsage.app_id == app_id,
            DeveloperApiUsage.stat_date == today,
            DeveloperApiUsage.stat_hour == hour,
            DeveloperApiUsage.endpoint == endpoint,
            DeveloperApiUsage.method == method,
            DeveloperApiUsage.status_code == status_code,
        ).first()

        if existing:
            existing.call_count += 1
        else:
            db.add(DeveloperApiUsage(
                app_id=app_id,
                organization_id=org_id,
                endpoint=endpoint,
                method=method,
                status_code=status_code,
                duration_ms=duration_ms,
                stat_date=today,
                stat_hour=hour,
                call_count=1,
            ))
        db.commit()
    except Exception:
        pass


# ── P13-5: 幂等键 dependency ──────────────────────────────────────────────────

_IK_TTL = 86400  # 24 小时


class IdempotencyCheck:
    """FastAPI Dependency：读取 Idempotency-Key header，24h 内相同 key 返回首次响应。

    用法：
        @router.post("/invoices")
        def create_invoice(..., ik: IdempotencyCheck = Depends(IdempotencyCheck("legal_invoices"))):
            if ik.hit:
                return ik.cached_response
            ...
            ik.store(result)
    """

    def __init__(self, resource_type: str):
        self.resource_type = resource_type

    def __call__(self, idempotency_key: Optional[str] = Header(None)):
        self.key = idempotency_key
        self.hit = False
        self.cached_response = None
        if not idempotency_key:
            return self

        r = redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)
        redis_key = f"idem:{self.resource_type}:{idempotency_key}"
        cached = r.get(redis_key)
        if cached:
            import json
            self.hit = True
            self.cached_response = json.loads(cached)
        return self

    def store(self, response_data: dict, db: Session = None) -> None:
        if not self.key:
            return
        import json
        r = redis_lib.from_url(get_settings().REDIS_URL, decode_responses=True)
        redis_key = f"idem:{self.resource_type}:{self.key}"
        existing = r.get(redis_key)
        if existing:
            from app.core.error_codes import err, IDEMPOTENCY_KEY_CONFLICT
            cached = json.loads(existing)
            # 如果已存在但响应不同，返回 409
            if cached != response_data:
                raise HTTPException(409, detail=err(IDEMPOTENCY_KEY_CONFLICT))
        r.setex(redis_key, _IK_TTL, json.dumps(response_data, default=str))
