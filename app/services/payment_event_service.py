"""支付 Webhook 事件服务（app/services/payment_event_service.py）。

可靠幂等与乱序处理：
- 验签 fail-closed：PAYMENT_WEBHOOK_REQUIRE_SIGNATURE 开启时，secret 缺失/签名
  无效/时间戳过期均拒绝，且不得修改账单状态（记录安全审计）。
- ``provider + provider_event_id`` 数据库唯一约束幂等；重复回调返回已存在事件，
  不重复执行任何副作用。
- 事件先落库（pending，仅存脱敏载荷 + hash），由 dispatch_payment_events_task
  异步 claim 处理；失败可重放、断点恢复。
- 乱序：按 occurred_at 与当前订阅 updated_at 比较，旧事件不覆盖新状态；
  无法排序/状态冲突 → needs_reconciliation + 审计信号。
- 归属：优先经 payment_subscription_id / payment_customer_id 映射验证；
  仅当无映射时回退 metadata.user_id（须用户存在）并记录安全审计。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as _time
from datetime import timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability import structured_log_json
from app.core.time import utc_now
from app.models.payment_event import PaymentEvent
from app.models.user import User

# 不落库/不处理用的敏感载荷键（卡数据、密钥、令牌等）
_SENSITIVE_PAYLOAD_KEYS = frozenset({
    "card", "fingerprint", "secret", "api_key", "api_secret", "token",
    "number", "cvv", "cvc", "exp_month", "exp_year", "client_secret", "password",
})


class WebhookRejectedError(ValueError):
    """Webhook 拒绝（签名/格式/归属），不产生副作用。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _redact(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: ("***" if k.lower() in _SENSITIVE_PAYLOAD_KEYS else _redact(v, key=k))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class PaymentEventService:

    # ── 验签（fail-closed）─────────────────────────────────────────

    def verify_signature(self, raw_body: bytes, signature: str | None,
                         *, require: bool | None = None) -> None:
        settings = get_settings()
        require = settings.PAYMENT_WEBHOOK_REQUIRE_SIGNATURE if require is None else require
        secret = settings.PAYMENT_WEBHOOK_SECRET or ""
        if not secret:
            if require:
                raise WebhookRejectedError("WEBHOOK_SIGNATURE_NOT_CONFIGURED",
                                           "支付 Webhook 验签密钥未配置，拒绝事件")
            return  # 显式关闭验签（仅测试/开发）

        if not signature:
            raise WebhookRejectedError("INVALID_WEBHOOK_SIGNATURE", "缺少供应商签名头")
        parts = dict(p.split("=", 1) for p in signature.split(",") if "=" in p)
        timestamp = parts.get("t", "")
        provided = parts.get("v1", "")
        if not timestamp or not provided:
            raise WebhookRejectedError("INVALID_WEBHOOK_SIGNATURE", "签名头格式不正确")
        try:
            timestamp_int = int(timestamp)
        except ValueError:
            raise WebhookRejectedError("INVALID_WEBHOOK_SIGNATURE", "签名时间戳非法")
        if abs(int(_time.time()) - timestamp_int) > settings.PAYMENT_WEBHOOK_SIGNATURE_TOLERANCE_SECONDS:
            raise WebhookRejectedError("WEBHOOK_SIGNATURE_EXPIRED", "Webhook 签名时间戳已过期")
        expected = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp}.{raw_body.decode('utf-8')}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise WebhookRejectedError("INVALID_WEBHOOK_SIGNATURE", "Webhook 签名无效")

    # ── 幂等持久化 ─────────────────────────────────────────────────

    @staticmethod
    def _parse_occurred(payload: dict, received_at) -> Any:
        raw = payload.get("created") or payload.get("occurred_at")
        if not raw:
            return None
        try:
            from datetime import datetime, timezone as _tz
            if isinstance(raw, (int, float)):
                return datetime.fromtimestamp(float(raw), tz=_tz.utc).replace(tzinfo=None)
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError, OverflowError):
            return None

    def record_event(
        self,
        *,
        db: Session,
        provider: str,
        provider_event_id: str,
        event_type: str,
        raw_payload: dict,
        received_at=None,
    ) -> PaymentEvent:
        """幂等登记事件：已存在返回既有（不重复处理）。"""
        received_at = received_at or utc_now()
        raw_hash = hashlib.sha256(
            json.dumps(raw_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        sanitized = json.dumps(_redact(raw_payload), ensure_ascii=False, sort_keys=True)

        existing = db.query(PaymentEvent).filter(
            PaymentEvent.provider == provider,
            PaymentEvent.provider_event_id == provider_event_id,
        ).first()
        if existing is not None:
            return existing

        data = raw_payload.get("data", {}) if isinstance(raw_payload.get("data"), dict) else {}
        obj = data.get("object", {}) if isinstance(data.get("object"), dict) else {}
        event = PaymentEvent(
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
            raw_payload_hash=raw_hash,
            sanitized_payload_json=sanitized,
            object_type="subscription" if event_type.startswith("customer.subscription")
            else (obj.get("object") if isinstance(obj.get("object"), str) else None),
            object_id=str(obj.get("id") or ""),
            occurred_at=self._parse_occurred(raw_payload, received_at),
            received_at=received_at,
            status="pending",
        )
        db.add(event)
        try:
            db.commit()
            db.refresh(event)
        except IntegrityError:
            db.rollback()
            existing = db.query(PaymentEvent).filter(
                PaymentEvent.provider == provider,
                PaymentEvent.provider_event_id == provider_event_id,
            ).first()
            if existing is None:
                raise
            return existing
        return event

    # ── 领取与处理 ─────────────────────────────────────────────────

    def claim_pending(self, *, db: Session, owner: str, batch_size: int | None = None) -> list[PaymentEvent]:
        """keyset 原子领取 pending 事件（并发安全，同一事件只处理一次）。"""
        from sqlalchemy import text as sa_text

        settings = get_settings()
        batch = batch_size or settings.PAYMENT_EVENT_CLAIM_BATCH_SIZE
        now = utc_now()
        stmt = sa_text(
            "UPDATE payment_events SET status='processing', claimed_by=:owner, "
            "claim_expires_at=:exp "
            "WHERE id IN ("
            "  SELECT id FROM payment_events "
            "  WHERE status = 'pending' "
            "  AND (next_retry_at IS NULL OR next_retry_at <= :now) "
            "  AND (claim_expires_at IS NULL OR claim_expires_at < :now) "
            "  ORDER BY id LIMIT :batch"
            ")"
        )
        db.execute(stmt, {
            "owner": owner, "exp": now + timedelta(seconds=settings.PAYMENT_EVENT_CLAIM_TTL_SECONDS),
            "now": now, "batch": batch,
        })
        db.commit()
        return (
            db.query(PaymentEvent)
            .filter(PaymentEvent.claimed_by == owner, PaymentEvent.status == "processing")
            .order_by(PaymentEvent.id.asc())
            .all()
        )

    def reclaim_stale(self, *, db: Session) -> int:
        """回收租约过期的 processing 事件（worker 崩溃后安全重领）。"""
        from sqlalchemy import text as sa_text

        settings = get_settings()
        stale_before = utc_now() - timedelta(seconds=settings.PAYMENT_EVENT_CLAIM_TTL_SECONDS)
        rows = db.query(PaymentEvent.id).filter(
            PaymentEvent.status == "processing",
            PaymentEvent.claim_expires_at.isnot(None),
            PaymentEvent.claim_expires_at < stale_before,
        ).limit(200).all()
        for (eid,) in rows:
            db.execute(sa_text(
                "UPDATE payment_events SET status='pending', claimed_by=NULL, "
                "claim_expires_at=NULL, error_code='LEASE_EXPIRED' WHERE id=:eid"
            ), {"eid": eid})
        db.commit()
        return len(rows)

    def process_event(self, db: Session, event: PaymentEvent) -> str:
        """处理单条事件；返回终态（completed / needs_reconciliation）。失败计入重试。

        已成功处理的事件重放为 no-op（不重复产生副作用）。
        """
        if event.status == "completed":
            return "completed"
        settings = get_settings()
        try:
            outcome = self._dispatch(db, event)
            if outcome == "needs_reconciliation":
                event.status = "needs_reconciliation"
            else:
                event.status = "completed"
            event.processed_at = utc_now()
            event.error_code = None
            db.commit()
            return event.status
        except WebhookRejectedError as exc:
            event.status = "needs_reconciliation"
            event.error_code = exc.code
            event.error_summary = exc.args[0][:500]
            event.processed_at = utc_now()
            db.commit()
            structured_log_json(source="payment_event", module="payment", action="webhook_rejected",
                                actor="-", target_type=event.object_type or "-",
                                target_id=event.object_id or event.provider_event_id,
                                detail=f"code={exc.code}")
            return event.status
        except Exception as exc:  # noqa: BLE001 - 统一记账，不吞错误
            event.attempt = (event.attempt or 0) + 1
            event.error_code = type(exc).__name__[:64]
            event.error_summary = "事件处理失败，可重试"[:500]
            if (event.attempt or 0) < settings.PAYMENT_EVENT_MAX_ATTEMPTS:
                from app.core.external_resilience import compute_backoff_delay
                delay = compute_backoff_delay(
                    event.attempt, base_seconds=settings.PAYMENT_EVENT_BACKOFF_BASE_SECONDS,
                    jitter=settings.EXTERNAL_BACKOFF_JITTER,
                    max_wait_seconds=settings.EXTERNAL_MAX_WAIT_SECONDS,
                )
                event.status = "pending"
                event.next_retry_at = utc_now() + timedelta(seconds=delay)
            else:
                event.status = "needs_reconciliation"
            event.claimed_by = None
            event.claim_expires_at = None
            db.commit()
            return event.status

    # ── 分发与状态应用 ─────────────────────────────────────────────

    def _dispatch(self, db: Session, event: PaymentEvent) -> str:
        payload = json.loads(event.sanitized_payload_json or "{}")
        data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
        obj = data.get("object", {}) if isinstance(data.get("object"), dict) else {}

        if event.provider == "stripe":
            if event.event_type in ("customer.subscription.created", "customer.subscription.updated"):
                return self._activate_from_event(db, event, obj)
            if event.event_type == "customer.subscription.deleted":
                return self._cancel_from_event(db, event, obj)
            return "needs_reconciliation"
        if event.provider == "pingpp":
            if event.event_type == "charge.succeeded":
                return self._activate_from_event(db, event, obj)
            return "needs_reconciliation"
        return "needs_reconciliation"

    def _resolve_user(self, db: Session, event: PaymentEvent, obj: dict) -> User | None:
        """归属解析：优先 provider 对象映射，其次 metadata（须用户存在，记录安全审计）。"""
        from app.models.subscription import UserSubscription

        object_id = str(obj.get("id") or "")
        customer = str(obj.get("customer") or "")
        if object_id:
            sub = db.query(UserSubscription).filter(
                UserSubscription.payment_subscription_id == object_id).first()
            if sub:
                return db.query(User).filter(User.id == sub.user_id).first()
        if customer:
            sub = db.query(UserSubscription).filter(
                UserSubscription.payment_customer_id == customer).first()
            if sub:
                return db.query(User).filter(User.id == sub.user_id).first()
        # 回退 metadata（须用户存在），记录安全审计
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        try:
            user_id = int(metadata.get("user_id") or 0)
        except (TypeError, ValueError):
            return None
        user = db.query(User).filter(User.id == user_id).first() if user_id else None
        if user is None:
            return None
        structured_log_json(source="payment_event", module="payment",
                            action="webhook_attribution_metadata",
                            actor=str(user.id), target_type="user_subscription",
                            target_id=object_id or event.provider_event_id,
                            detail="attribution_via_metadata_user_id")
        return user

    def _activate_from_event(self, db: Session, event: PaymentEvent, obj: dict) -> str:
        """订阅激活（乱序保护：旧事件不覆盖新状态）。"""
        from app.models.subscription import UserSubscription
        from app.services.subscription_service import subscription_service

        object_id = str(obj.get("id") or "")
        customer = str(obj.get("customer") or "")
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        plan_tier = str(metadata.get("plan_tier") or "pro")
        user = self._resolve_user(db, event, obj)
        if user is None:
            return "needs_reconciliation"

        # 乱序保护：若已有同 provider 订阅且其记录时间晚于本事件，跳过（不回退）
        existing = db.query(UserSubscription).filter(
            UserSubscription.payment_subscription_id == object_id).first() if object_id else None
        if existing is not None:
            last_known = existing.updated_at or existing.created_at
            if last_known is not None and event.occurred_at is not None and last_known > event.occurred_at:
                return "completed"  # 旧事件已落后，跳过

        subscription_service.activate_subscription(
            db=db,
            user_id=user.id,
            plan_tier=plan_tier,
            payment_provider=event.provider,
            payment_subscription_id=object_id,
            payment_customer_id=customer or None,
            idempotency_key=f"webhook:{event.provider}:{object_id}:{event.event_type}",
            reason=f"webhook:{event.event_type}",
        )
        return "completed"

    def _cancel_from_event(self, db: Session, event: PaymentEvent, obj: dict) -> str:
        """订阅取消（乱序保护）。"""
        from app.models.subscription import UserSubscription
        from app.services.subscription_service import subscription_service

        object_id = str(obj.get("id") or "")
        sub = db.query(UserSubscription).filter(
            UserSubscription.payment_subscription_id == object_id).first() if object_id else None
        if sub is None:
            return "needs_reconciliation"
        last_known = sub.updated_at or sub.created_at
        if last_known is not None and event.occurred_at is not None and last_known > event.occurred_at:
            return "completed"  # 后到的旧 deleted 事件不覆盖更新的 created
        subscription_service.cancel_by_provider_id(db, object_id, reason="webhook:subscription.deleted")
        return "completed"

    # ── 对外入口（API 调用）────────────────────────────────────────

    def handle_webhook(self, *, db: Session, provider: str, event_type: str,
                       raw_body: bytes, payload: dict, signature: str | None) -> PaymentEvent:
        """API 入口：验签 → 幂等登记事件（快速返回，异步处理）。"""
        self.verify_signature(raw_body, signature)
        data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
        obj = data.get("object", {}) if isinstance(data.get("object"), dict) else {}
        provider_event_id = (str(payload.get("id") or payload.get("provider_event_id") or "")
                             or str(obj.get("id") or ""))
        if not provider_event_id:
            raise WebhookRejectedError("INVALID_PAYLOAD", "Webhook 缺少事件 ID")
        return self.record_event(
            db=db, provider=provider, provider_event_id=provider_event_id,
            event_type=event_type, raw_payload=payload,
        )


payment_event_service = PaymentEventService()
