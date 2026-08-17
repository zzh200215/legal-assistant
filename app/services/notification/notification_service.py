"""Phase 11 — 通知服务（Outbox 化投递）

统一管理站内通知、邮件通知的创建、投递（Outbox 领取）与偏好。
- 业务事务内创建 LegalNotificationEvent（同幂等键去重），投递由 worker 领取执行。
- 邮件渠道真实投递：创建 EmailDraft + EmailSendRequest（EmailSendRequest 即邮件
  Outbox），自动批准的低风险通知直接入队，需审批的等待人工审批。
- 状态机由本服务集中校验；投递列（attempt/next_retry_at/claimed_by/claim_expires_at）
  支持 worker 原子领取与崩溃回收。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import or_ as sa_or
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability import structured_log_json
from app.core.obs_context import get_context
from app.core.time import utc_now
from app.models.legal_notifications import (
    LegalNotificationEvent,
    LegalNotificationPreference,
)
from app.models.user import User
from app.services.observability.oplog_service import oplog_service

logger = logging.getLogger(__name__)


def _record_delivery_metric(event: LegalNotificationEvent, status: str) -> None:
    """通知投递终态指标（P1，非阻塞；channel/status 有限枚举）。"""
    try:
        from app.core.metrics import metrics

        metrics.increment(
            "notification_deliveries",
            labels={"channel": event.channel, "status": status},
        )
    except Exception:  # noqa: BLE001 - 指标失败不影响业务
        pass

# 通知渠道优先级：站内始终投递，其他渠道按偏好和可用性决定
CHANNEL_SITE = "site"
CHANNEL_EMAIL = "email"
CHANNEL_WECHAT = "wechat"
CHANNEL_FEISHU = "feishu"
ALL_CHANNELS = (CHANNEL_SITE, CHANNEL_EMAIL, CHANNEL_WECHAT, CHANNEL_FEISHU)

# 事件类型
EVENT_TYPES = ("deadline", "approval", "invoice", "sign", "portal", "all")

# ── 通知状态机（LegalNotificationEvent 作为通知 Outbox）────────────────────────
# pending(=requested) -> approved -> sending -> sent / delivered / failed / dead_letter
# 恢复：failed -> pending / dead_letter；dead_letter -> pending（人工）
STATUS_PENDING = "pending"          # 已请求发送（等价 requested），等待审批或自动批准
STATUS_APPROVED = "approved"        # 审批通过，允许进入投递队列
STATUS_REJECTED = "rejected"        # 审批拒绝
STATUS_SENDING = "sending"          # worker 已 claim，投递中
STATUS_SENT = "sent"                # 邮件投递成功（provider 确认）
STATUS_DELIVERED = "delivered"      # 站内投递完成
STATUS_READ = "read"                # 已读
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_ESCALATED = "escalated"
STATUS_FAILED = "failed"            # 可重试失败
STATUS_DEAD_LETTER = "dead_letter"  # 超过最大尝试次数或不可恢复

_NOTIFICATION_TRANSITIONS: dict[str, frozenset] = {
    STATUS_PENDING: frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_SENDING,
                               STATUS_FAILED, STATUS_DEAD_LETTER}),
    STATUS_APPROVED: frozenset({STATUS_SENDING, STATUS_SENT, STATUS_DELIVERED,
                                STATUS_FAILED, STATUS_DEAD_LETTER, STATUS_REJECTED}),
    STATUS_SENDING: frozenset({STATUS_SENT, STATUS_DELIVERED, STATUS_FAILED,
                               STATUS_DEAD_LETTER, STATUS_PENDING, STATUS_APPROVED}),
    STATUS_FAILED: frozenset({STATUS_PENDING, STATUS_DEAD_LETTER}),
    STATUS_DEAD_LETTER: frozenset({STATUS_PENDING}),
    STATUS_REJECTED: frozenset({STATUS_PENDING}),
    STATUS_SENT: frozenset({STATUS_READ, STATUS_ACKNOWLEDGED}),
    STATUS_DELIVERED: frozenset({STATUS_READ, STATUS_ACKNOWLEDGED}),
    STATUS_READ: frozenset(),
    STATUS_ACKNOWLEDGED: frozenset(),
    STATUS_ESCALATED: frozenset(),
}


class NotificationStateError(ValueError):
    """通知状态机非法跳转。"""


class NotificationService:

    # ── 状态机 ──────────────────────────────────────────────────────

    def transition(self, *, db: Session, event: LegalNotificationEvent, to: str,
                   reason: str | None = None) -> None:
        """集中校验并执行通知状态迁移；禁止调用方任意改状态。"""
        current = event.status
        allowed = _NOTIFICATION_TRANSITIONS.get(current, frozenset())
        if to not in allowed:
            raise NotificationStateError(f"通知状态机拒绝迁移: {current} -> {to}")
        event.status = to
        structured_log_json(
            source="notification", module="notification", action=f"notification_{to}",
            actor=str(event.user_id), target_type="notification_event", target_id=str(event.id),
            detail=reason or f"from={current}",
        )

    def mark_approved(self, db: Session, event: LegalNotificationEvent) -> None:
        self.transition(db=db, event=event, to=STATUS_APPROVED)
        event.claim_expires_at = None
        event.next_retry_at = None

    def mark_sent(self, db: Session, event: LegalNotificationEvent,
                  provider_message_id: str | None = None) -> None:
        self.transition(db=db, event=event, to=STATUS_SENT)
        event.sent_at = utc_now()
        if provider_message_id:
            event.provider_message_id = provider_message_id
        event.claim_expires_at = None
        _record_delivery_metric(event, "sent")

    def mark_failed(self, db: Session, event: LegalNotificationEvent,
                    error_code: str | None = None) -> None:
        self.transition(db=db, event=event, to=STATUS_FAILED)
        if error_code:
            event.error_code = error_code
        event.claim_expires_at = None
        settings = get_settings()
        delay = settings.NOTIFICATION_BACKOFF_BASE_SECONDS * (2 ** max(0, (event.attempt or 1) - 1))
        event.next_retry_at = utc_now() + timedelta(seconds=delay)
        _record_delivery_metric(event, "failed")

    def mark_dead_letter(self, db: Session, event: LegalNotificationEvent,
                         reason: str, error_code: str | None = None) -> None:
        self.transition(db=db, event=event, to=STATUS_DEAD_LETTER)
        if error_code:
            event.error_code = error_code
        event.sanitized_error_message = reason
        event.claim_expires_at = None
        oplog_service.log(module="notification", action="notification_dead_letter", db=db,
                          user_id=event.user_id, target_type="notification_event", target_id=event.id,
                          detail=f"error_code={error_code or 'unknown'}; reason={reason}")
        _record_delivery_metric(event, "dead_letter")

    # ── 创建通知（幂等键去重）─────────────────────────────────────────

    @staticmethod
    def _idempotency_key(organization_id: int, user_id: int, channel: str, event_type: str,
                         reference_type: str | None, reference_id: int | None,
                         business_version: int | None) -> str:
        version = business_version if business_version is not None else 1
        return "notify:" + ":".join([
            str(organization_id), str(user_id), str(channel), str(event_type),
            str(reference_type or ""), str(reference_id or ""), str(version),
        ])

    def create_notification(self, *, db: Session, organization_id: int,
                           user_id: int, event_type: str, title: str,
                           body: str | None = None,
                           channel: str = CHANNEL_SITE,
                           case_id: int | None = None,
                           reference_type: str | None = None,
                           reference_id: int | None = None,
                           scheduled_at: datetime | None = None,
                           business_version: int | None = None,
                           template_key: str | None = None,
                           locale: str | None = None) -> LegalNotificationEvent:
        """创建一条通知事件（幂等：同幂等键重复请求返回已有记录，不重复创建）。

        站内通知是基础渠道，始终创建。邮件渠道仅登记事件，实际投递由 Outbox 领取。
        """
        if event_type not in EVENT_TYPES and event_type != "deadline_reminder":
            raise ValueError(f"不支持的事件类型: {event_type}")
        if channel not in ALL_CHANNELS:
            raise ValueError(f"不支持的通知渠道: {channel}")

        idem_key = self._idempotency_key(
            organization_id, user_id, channel, event_type,
            reference_type, reference_id, business_version,
        )
        existing = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.idempotency_key == idem_key
        ).first()
        if existing is not None:
            return existing  # 已去重：返回已有记录，保留审计

        # P1 链路关联：统一上下文 trace_id/request_id（API/Celery headers 传播）。
        ctx = get_context()
        event = LegalNotificationEvent(
            organization_id=organization_id,
            user_id=user_id,
            case_id=case_id,
            event_type=event_type,
            title=title,
            body=body,
            channel=channel,
            status=STATUS_PENDING,
            reference_type=reference_type,
            reference_id=reference_id,
            scheduled_at=scheduled_at,
            idempotency_key=idem_key,
            max_attempts=get_settings().NOTIFICATION_MAX_ATTEMPTS,
            template_key=template_key,
            locale=locale,
            trace_id=ctx.trace_id,
            request_id=ctx.request_id,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        oplog_service.log(module="notification", action="notification_created", db=db,
                          user_id=user_id, target_type="notification_event", target_id=event.id,
                          detail=f"channel={channel}; event_type={event_type}; idempotency_key={idem_key}")
        return event

    def create_multi_channel_notification(self, *, db: Session, organization_id: int,
                                          user_id: int, event_type: str, title: str,
                                          body: str | None = None,
                                          channels: list[str] | None = None,
                                          case_id: int | None = None,
                                          reference_type: str | None = None,
                                          reference_id: int | None = None,
                                          scheduled_at: datetime | None = None,
                                          business_version: int | None = None) -> list[LegalNotificationEvent]:
        """创建多渠道通知事件（幂等：每个渠道一个事件，同键去重）。

        根据 user 通知偏好决定哪些渠道生效。站内通知始终创建。
        """
        effective_channels = self._resolve_channels(
            db=db, user_id=user_id, organization_id=organization_id,
            event_type=event_type, requested_channels=channels,
        )

        events: list[LegalNotificationEvent] = []
        for channel in effective_channels:
            event = self.create_notification(
                db=db,
                organization_id=organization_id,
                user_id=user_id,
                event_type=event_type,
                title=title,
                body=body,
                channel=channel,
                case_id=case_id,
                reference_type=reference_type,
                reference_id=reference_id,
                scheduled_at=scheduled_at,
                business_version=business_version,
            )
            events.append(event)
        return events

    def _resolve_channels(self, *, db: Session, user_id: int, organization_id: int,
                          event_type: str, requested_channels: list[str] | None) -> list[str]:
        """根据偏好和可用性解析实际发送渠道。"""
        # 站内通知始终包含
        channels = [CHANNEL_SITE]

        if not requested_channels:
            # 默认只发站内 + 邮件
            requested_channels = [CHANNEL_SITE, CHANNEL_EMAIL]

        pref = self._get_preference(db=db, user_id=user_id, organization_id=organization_id,
                                    event_type=event_type)

        for ch in requested_channels:
            if ch == CHANNEL_SITE:
                continue  # 已包含
            if ch == CHANNEL_EMAIL:
                if self._is_email_available(db, organization_id):
                    if pref and not self._channel_in_pref(pref, ch):
                        continue
                    channels.append(ch)
            elif ch == CHANNEL_WECHAT:
                if self._is_wechat_available(db, user_id):
                    if pref and not self._channel_in_pref(pref, ch):
                        continue
                    channels.append(ch)
            elif ch == CHANNEL_FEISHU:
                if self._is_feishu_available(db, user_id):
                    if pref and not self._channel_in_pref(pref, ch):
                        continue
                    channels.append(ch)

        return channels

    def _get_preference(self, *, db: Session, user_id: int, organization_id: int,
                        event_type: str) -> LegalNotificationPreference | None:
        """获取用户通知偏好，优先匹配具体事件类型，回退到 all。"""
        pref = db.query(LegalNotificationPreference).filter(
            LegalNotificationPreference.user_id == user_id,
            LegalNotificationPreference.organization_id == organization_id,
            LegalNotificationPreference.event_type == event_type,
        ).first()
        if not pref:
            pref = db.query(LegalNotificationPreference).filter(
                LegalNotificationPreference.user_id == user_id,
                LegalNotificationPreference.organization_id == organization_id,
                LegalNotificationPreference.event_type == "all",
            ).first()
        return pref

    def _channel_in_pref(self, pref: LegalNotificationPreference, channel: str) -> bool:
        """检查偏好中是否启用了某渠道。"""
        try:
            channels = json.loads(pref.channels_json or "[]")
            return channel in channels
        except (json.JSONDecodeError, TypeError):
            return False

    def _is_email_available(self, db: Session, organization_id: int) -> bool:
        """检查组织是否配置了外发邮件。"""
        try:
            from app.models.email import OutboundEmailPolicy
            policy = db.query(OutboundEmailPolicy).filter(
                OutboundEmailPolicy.organization_id == organization_id
            ).first()
            if not policy:
                # 回退到全局策略
                policy = db.query(OutboundEmailPolicy).filter(
                    OutboundEmailPolicy.organization_id.is_(None)
                ).first()
            return bool(policy and policy.enabled)
        except Exception:
            return False

    def _is_wechat_available(self, db: Session, user_id: int) -> bool:
        """检查用户是否授权了微信连接器。"""
        try:
            from app.models.user import WechatUser
            wechat = db.query(WechatUser).filter(WechatUser.user_id == user_id).first()
            return wechat is not None
        except Exception:
            return False

    def _is_feishu_available(self, db: Session, user_id: int) -> bool:
        """检查用户是否授权了飞书连接器。"""
        try:
            from app.models.connector import ExternalConnector
            connector = db.query(ExternalConnector).filter(
                ExternalConnector.connector_type == "feishu",
                ExternalConnector.status == "active",
            ).first()
            return connector is not None
        except Exception:
            return False

    # ── 投递通知（Outbox 领取）──────────────────────────────────────

    def _claim_events(self, db: Session, owner: str, now: datetime, batch_size: int) -> list[LegalNotificationEvent]:
        """keyset 原子领取待投递事件（并发安全，多 worker 同一记录只投递一次）。

        仅领取：status=pending/approved、已到期（next_retry_at/claim_expires_at）、
        scheduled_at 已到、且邮件渠道尚未创建投递请求（避免重复建 Outbox）。
        """
        settings = get_settings()
        ttl = settings.NOTIFICATION_CLAIM_TTL_SECONDS
        claim_exp = now + timedelta(seconds=ttl)
        stmt = sa_text(
            "UPDATE legal_notification_events SET status=:sending, claimed_by=:owner, "
            "claim_expires_at=:exp "
            "WHERE id IN ("
            "  SELECT id FROM legal_notification_events "
            "  WHERE status IN (:st1, :st2) "
            "  AND (next_retry_at IS NULL OR next_retry_at <= :now) "
            "  AND (claim_expires_at IS NULL OR claim_expires_at < :now) "
            "  AND (scheduled_at IS NULL OR scheduled_at <= :now) "
            "  AND (channel <> 'email' OR email_send_request_id IS NULL) "
            "  ORDER BY id LIMIT :batch"
            ")"
        )
        db.execute(stmt, {
            "sending": STATUS_SENDING, "owner": owner, "exp": claim_exp,
            "st1": STATUS_PENDING, "st2": STATUS_APPROVED, "now": now, "batch": batch_size,
        })
        db.commit()
        return (
            db.query(LegalNotificationEvent)
            .filter(LegalNotificationEvent.claimed_by == owner,
                    LegalNotificationEvent.status == STATUS_SENDING)
            .order_by(LegalNotificationEvent.id.asc())
            .all()
        )

    def dispatch_pending(self, *, db: Session) -> dict:
        """投递待发送通知（Outbox 领取批次，返回统计）。"""
        settings = get_settings()
        batch = settings.NOTIFICATION_CLAIM_BATCH_SIZE
        owner = f"dispatch:{uuid.uuid4().hex}"
        stats = {"delivered": 0, "failed": 0, "skipped": 0}
        total = 0
        while total < batch * 20:
            events = self._claim_events(db, owner, utc_now(), batch)
            if not events:
                break
            for event in events:
                result = self._dispatch_event(db, event)
                if result in ("delivered", "sent"):
                    stats["delivered"] += 1
                elif result == "failed":
                    stats["failed"] += 1
                else:
                    stats["skipped"] += 1
            db.commit()
            total += len(events)
        return stats

    def _dispatch_event(self, db: Session, event: LegalNotificationEvent) -> str:
        """投递单条通知（事件已被 claim，status=sending）。返回 delivered/sent/failed/skipped。"""
        # P1：通知投递子 span（仅 channel/event_type 元数据）。
        from app.core.telemetry import observe_span

        with observe_span("notification.deliver", attributes={
            "channel": event.channel,
            "event_type": event.event_type,
        }):
            try:
                if event.channel == CHANNEL_SITE:
                    return self._dispatch_site(db, event)
                elif event.channel == CHANNEL_EMAIL:
                    return self._dispatch_email(db, event)
                elif event.channel in (CHANNEL_WECHAT, CHANNEL_FEISHU):
                    return self._dispatch_external_channel(db, event)
                else:
                    self.mark_dead_letter(db, event, reason="未知通知渠道", error_code="UNKNOWN_CHANNEL")
                    return "failed"
            except NotificationStateError:
                raise
            except Exception as exc:
                logger.error("通知投递异常 event_id=%s: %s", event.id, exc)
                self.mark_failed(db, event, error_code=type(exc).__name__[:64])
                return "failed"

    def _dispatch_site(self, db: Session, event: LegalNotificationEvent) -> str:
        """站内通知：直接标记为 delivered（进入铃铛未读）。"""
        event.attempt = (event.attempt or 0) + 1
        self.transition(db=db, event=event, to=STATUS_DELIVERED)
        event.sent_at = utc_now()
        _record_delivery_metric(event, "delivered")
        return "delivered"

    def _dispatch_email(self, db: Session, event: LegalNotificationEvent) -> str:
        """邮件通知：真实投递（创建 EmailDraft + EmailSendRequest 邮件 Outbox）。

        - DLP block → dead_letter；review_required → 需人工审批。
        - 内部低风险自动批准 → 事件 approved，由邮件 worker 发送。
        - 需审批 → 事件回退 pending 等待审批（保持租约避免重复领取）。
        """
        from app.services.notification.outbound_email_service import EMAIL_REQ_APPROVED, outbound_email_service

        if event.status == STATUS_PENDING:
            self.transition(db=db, event=event, to=STATUS_SENDING)
        if event.email_send_request_id:
            # 已交由邮件 Outbox，等待审批/投递 → 回退等待态，避免重复创建
            self.transition(db=db, event=event, to=STATUS_PENDING)
            event.claim_expires_at = utc_now() + timedelta(
                seconds=get_settings().NOTIFICATION_CLAIM_TTL_SECONDS)
            return "skipped"

        user = db.query(User).filter(User.id == event.user_id).first()
        if not user or not user.email:
            self.mark_dead_letter(db, event, reason="用户无邮箱，无法发送邮件通知", error_code="NO_USER_EMAIL")
            return "failed"

        subject, body = self._render_email_content(db, event)
        if subject is None:
            self.mark_dead_letter(db, event, reason="通知模板渲染失败", error_code="TEMPLATE_ERROR")
            return "failed"

        # DLP 门禁（含收件人）：block → dead letter；review → 强制人工审批
        from app.services.notification.dlp_scanner import dlp_scanner
        dlp = dlp_scanner.scan(payloads=[subject, body, user.email], action="block")
        if dlp.blocked:
            self.mark_dead_letter(db, event, reason="通知内容命中高风险 DLP 策略",
                                  error_code="DLP_BLOCKED")
            return "failed"
        auto_approve = (not dlp.requires_review
                        and get_settings().AUTO_APPROVE_EMAIL_NOTIFICATION_TO_OWNER)

        request = outbound_email_service.create_notification_email(
            db=db, user=user, notification_event=event,
            subject=subject, body=body, recipient=user.email, auto_approve=auto_approve,
        )
        if request is None:
            self.mark_dead_letter(db, event, reason="无可用 SMTP 连接器或外发策略禁用",
                                  error_code="NO_SMTP_CONNECTOR")
            return "failed"

        event.attempt = (event.attempt or 0) + 1
        if auto_approve and request.status == EMAIL_REQ_APPROVED:
            self.mark_approved(db, event)
            return "sent"
        # 需人工审批：回退等待态，保留租约防止重复领取
        self.transition(db=db, event=event, to=STATUS_PENDING)
        event.claim_expires_at = utc_now() + timedelta(
            seconds=get_settings().NOTIFICATION_CLAIM_TTL_SECONDS)
        return "skipped"

    def _render_email_content(self, db: Session, event: LegalNotificationEvent) -> tuple[str | None, str | None]:
        """渲染邮件通知内容：配置了模板则按模板渲染，否则用事件标题/正文。"""
        if not event.template_key:
            return event.title, event.body or ""
        try:
            from app.services.notification.notification_template_service import notification_template_service
            rendered = notification_template_service.render(
                db=db, channel=CHANNEL_EMAIL, template_key=event.template_key,
                locale=event.locale or "zh-CN",
                params={"title": event.title, "body": event.body or "",
                        "reference_id": str(event.reference_id or "")},
            )
            return rendered.get("subject") or event.title, rendered.get("body") or ""
        except Exception as exc:  # noqa: BLE001 - 渲染失败按模板错误处理
            logger.error("通知模板渲染失败 event_id=%s: %s", event.id, exc)
            return None, None

    def _dispatch_external_channel(self, db: Session, event: LegalNotificationEvent) -> str:
        """微信/飞书通知：保留既有占位投递（无真实出站凭据，标记为 sent）。"""
        try:
            from app.models.connector import ExternalConnector
            connector_type = "wecom" if event.channel == CHANNEL_WECHAT else "feishu"
            connector = db.query(ExternalConnector).filter(
                ExternalConnector.connector_type == connector_type,
                ExternalConnector.status == "active",
            ).first()
            if not connector:
                self.mark_failed(db, event, error_code="NO_CONNECTOR")
                return "failed"
            event.attempt = (event.attempt or 0) + 1
            self.mark_sent(db, event)
            return "sent"
        except Exception as exc:
            logger.error("外部渠道通知发送失败: %s", exc)
            self.mark_failed(db, event, error_code="EXTERNAL_CHANNEL_FAILED")
            return "failed"

    def send_email_notification(self, *, db: Session, user_id: int,
                                organization_id: int, subject: str, body: str,
                                event_type: str | None = None,
                                reference_type: str | None = None,
                                reference_id: int | None = None,
                                event_id: int | None = None) -> bool:
        """真实发送邮件通知（Outbox）：创建 EmailDraft + EmailSendRequest。

        兼容旧签名：可按 event_id 或 reference 定位目标事件。返回是否已登记投递。
        """
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not user.email:
            logger.warning("用户 %s 无邮箱，跳过邮件通知", user_id)
            return False

        target = None
        if event_id:
            target = db.query(LegalNotificationEvent).filter(
                LegalNotificationEvent.id == event_id).first()
        elif reference_type and reference_id:
            target = db.query(LegalNotificationEvent).filter(
                LegalNotificationEvent.reference_type == reference_type,
                LegalNotificationEvent.reference_id == reference_id,
                LegalNotificationEvent.channel == CHANNEL_EMAIL,
                LegalNotificationEvent.status.in_([STATUS_PENDING, STATUS_SENDING]),
            ).order_by(LegalNotificationEvent.id.desc()).first()
        if target is None:
            return False
        return self._dispatch_email(db, target) in ("sent", "skipped")

    # ── 失败重试 / 死信 ──────────────────────────────────────────────

    def retry_failed(self, *, db: Session, max_retries: int = 3) -> int:
        """重试失败的通知事件（基于 attempt 列，不再污染 body）。"""
        now = utc_now()
        failed_events = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == STATUS_FAILED,
            LegalNotificationEvent.attempt < max_retries,
            sa_or(LegalNotificationEvent.next_retry_at.is_(None),
                  LegalNotificationEvent.next_retry_at <= now),
        ).limit(500).all()

        retried = 0
        for event in failed_events:
            event.attempt = (event.attempt or 0) + 1
            self.transition(db=db, event=event, to=STATUS_PENDING)
            event.next_retry_at = None
            event.claim_expires_at = None
            retried += 1
        if retried:
            db.commit()
        return retried

    def list_dead_letter(self, *, db: Session, user: User, limit: int = 50) -> list[LegalNotificationEvent]:
        """查看死信通知（admin 同组织可见，普通用户仅本人）。"""
        query = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == STATUS_DEAD_LETTER)
        if user.role != "admin":
            query = query.filter(LegalNotificationEvent.user_id == user.id)
        return query.order_by(LegalNotificationEvent.updated_at.desc()).limit(limit).all()

    def manual_retry(self, *, db: Session, event_id: int, user: User) -> LegalNotificationEvent:
        """人工重试死信通知：校验权限，保留原幂等键，完整审计。

        若关联的邮件 Outbox 请求已是死信，一并重置为待投递（级联重试），
        否则通知回 pending 后无人发送。
        """
        event = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.id == event_id).first()
        if not event:
            raise ValueError("通知不存在")
        if event.user_id != user.id and user.role != "admin":
            raise ValueError("无权重试该通知")
        if event.status != STATUS_DEAD_LETTER:
            raise ValueError("只有死信状态可人工重试")
        self.transition(db=db, event=event, to=STATUS_PENDING)
        event.attempt = 0
        event.next_retry_at = None
        event.claim_expires_at = None
        event.error_code = None
        event.sanitized_error_message = None
        # 级联：关联邮件请求为死信 → 一并重置待投递（权限已在通知层校验）
        if event.email_send_request_id:
            from app.models.email import EmailSendRequest
            from app.services.notification.outbound_email_service import outbound_email_service

            req = db.query(EmailSendRequest).filter(
                EmailSendRequest.id == event.email_send_request_id).first()
            if req is not None and req.status == "dead_letter":
                outbound_email_service._reset_dead_letter(req, db=db)
        db.commit()
        db.refresh(event)
        oplog_service.log(module="notification", action="notification_manual_retry", db=db,
                          user_id=user.id, target_type="notification_event", target_id=event.id,
                          detail=f"idempotency_key={event.idempotency_key}")
        return event

    # ── 通知偏好管理 ─────────────────────────────────────────────────

    def set_preference(self, *, db: Session, user_id: int, organization_id: int,
                       event_type: str, channels: list[str],
                       mute_start: str | None = None,
                       mute_end: str | None = None,
                       timezone_str: str = "Asia/Shanghai",
                       delegate_user_id: int | None = None,
                       summary_frequency: str | None = None) -> LegalNotificationPreference:
        """设置或更新用户通知偏好。"""
        if event_type not in EVENT_TYPES:
            raise ValueError(f"不支持的事件类型: {event_type}")
        for ch in channels:
            if ch not in ALL_CHANNELS:
                raise ValueError(f"不支持的通知渠道: {ch}")

        # 站内通知始终包含
        if CHANNEL_SITE not in channels:
            channels = [CHANNEL_SITE] + list(channels)

        pref = db.query(LegalNotificationPreference).filter(
            LegalNotificationPreference.user_id == user_id,
            LegalNotificationPreference.organization_id == organization_id,
            LegalNotificationPreference.event_type == event_type,
        ).first()

        if not pref:
            pref = LegalNotificationPreference(
                user_id=user_id,
                organization_id=organization_id,
                event_type=event_type,
            )
            db.add(pref)

        pref.channels_json = json.dumps(sorted(set(channels)), ensure_ascii=False)
        pref.mute_start = mute_start
        pref.mute_end = mute_end
        pref.timezone = timezone_str
        pref.delegate_user_id = delegate_user_id
        pref.summary_frequency = summary_frequency

        db.commit()
        db.refresh(pref)
        return pref

    def get_preference(self, *, db: Session, user_id: int,
                       organization_id: int) -> list[dict]:
        """获取用户所有通知偏好。"""
        prefs = db.query(LegalNotificationPreference).filter(
            LegalNotificationPreference.user_id == user_id,
            LegalNotificationPreference.organization_id == organization_id,
        ).all()
        return [self.serialize_preference(p) for p in prefs]

    # ── 通知读取与确认 ──────────────────────────────────────────────

    def get_user_notifications(self, *, db: Session, user_id: int,
                               status: str | None = None,
                               event_type: str | None = None,
                               limit: int = 50) -> list[LegalNotificationEvent]:
        """获取用户的站内通知列表。"""
        query = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == user_id,
            LegalNotificationEvent.channel == CHANNEL_SITE,
        )
        if status:
            query = query.filter(LegalNotificationEvent.status == status)
        if event_type:
            query = query.filter(LegalNotificationEvent.event_type == event_type)
        return query.order_by(
            LegalNotificationEvent.created_at.desc()
        ).limit(limit).all()

    def mark_as_read(self, *, db: Session, event_id: int, user_id: int) -> LegalNotificationEvent:
        """标记通知为已读。"""
        event = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.id == event_id,
            LegalNotificationEvent.user_id == user_id,
            LegalNotificationEvent.channel == CHANNEL_SITE,
        ).first()
        if not event:
            raise ValueError("通知不存在")
        if event.status in ("delivered", "sent"):
            self.transition(db=db, event=event, to=STATUS_READ)
            event.acknowledged_at = utc_now()
            db.commit()
            db.refresh(event)
        return event

    def mark_acknowledged(self, db: Session, event: LegalNotificationEvent) -> None:
        """确认通知（ack 端点）：正常路径走状态机，兼容历史任意状态直写。"""
        if event.status in (STATUS_SENT, STATUS_DELIVERED):
            self.transition(db=db, event=event, to=STATUS_ACKNOWLEDGED)
        else:
            event.status = STATUS_ACKNOWLEDGED
        event.acknowledged_at = utc_now()

    def mark_all_as_read(self, *, db: Session, user_id: int) -> int:
        """标记用户所有站内通知为已读。返回更新数量。"""
        unread = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == user_id,
            LegalNotificationEvent.channel == CHANNEL_SITE,
            LegalNotificationEvent.status.in_(["delivered", "sent"]),
        ).all()
        now = utc_now()
        count = 0
        for event in unread:
            self.transition(db=db, event=event, to=STATUS_READ)
            event.acknowledged_at = now
            count += 1
        db.commit()
        return count

    def get_unread_count(self, *, db: Session, user_id: int) -> int:
        """获取用户未读通知数量。"""
        return db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == user_id,
            LegalNotificationEvent.channel == CHANNEL_SITE,
            LegalNotificationEvent.status.in_(["delivered", "sent"]),
        ).count()

    # ── 序列化 ────────────────────────────────────────────────────

    def serialize_event(self, event: LegalNotificationEvent) -> dict:
        return {
            "id": event.id,
            "organization_id": event.organization_id,
            "user_id": event.user_id,
            "case_id": event.case_id,
            "event_type": event.event_type,
            "title": event.title,
            "body": event.body,
            "channel": event.channel,
            "status": event.status,
            "reference_type": event.reference_type,
            "reference_id": event.reference_id,
            "scheduled_at": event.scheduled_at.isoformat() if event.scheduled_at else None,
            "sent_at": event.sent_at.isoformat() if event.sent_at else None,
            "acknowledged_at": event.acknowledged_at.isoformat() if event.acknowledged_at else None,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "attempt": event.attempt,
            "max_attempts": event.max_attempts,
            "next_retry_at": event.next_retry_at.isoformat() if event.next_retry_at else None,
            "error_code": event.error_code,
            "provider_message_id": event.provider_message_id,
            "template_key": event.template_key,
            "template_version": event.template_version,
            "locale": event.locale,
        }

    def serialize_preference(self, pref: LegalNotificationPreference) -> dict:
        channels = []
        try:
            channels = json.loads(pref.channels_json or "[]")
        except (json.JSONDecodeError, TypeError):
            pass

        return {
            "id": pref.id,
            "user_id": pref.user_id,
            "organization_id": pref.organization_id,
            "event_type": pref.event_type,
            "channels": channels,
            "mute_start": pref.mute_start,
            "mute_end": pref.mute_end,
            "timezone": pref.timezone,
            "delegate_user_id": pref.delegate_user_id,
            "summary_frequency": pref.summary_frequency,
            "updated_at": pref.updated_at.isoformat() if pref.updated_at else None,
        }


notification_service = NotificationService()
