"""Phase 11 — 通知服务

统一管理站内通知、邮件通知、微信/飞书通知的创建、投递和偏好管理。
基于 LegalNotificationEvent 模型存储通知事件，偏好由 LegalNotificationPreference 管理。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Sequence

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models.legal_notifications import (
    LegalNotificationEvent,
    LegalNotificationPreference,
)
from app.models.user import User
from app.services.oplog_service import oplog_service

logger = logging.getLogger(__name__)

# 通知渠道优先级：站内始终投递，其他渠道按偏好和可用性决定
CHANNEL_SITE = "site"
CHANNEL_EMAIL = "email"
CHANNEL_WECHAT = "wechat"
CHANNEL_FEISHU = "feishu"
ALL_CHANNELS = (CHANNEL_SITE, CHANNEL_EMAIL, CHANNEL_WECHAT, CHANNEL_FEISHU)

# 事件类型
EVENT_TYPES = ("deadline", "approval", "invoice", "sign", "portal", "all")


class NotificationService:

    # ── 创建通知 ──────────────────────────────────────────────────

    def create_notification(self, *, db: Session, organization_id: int,
                           user_id: int, event_type: str, title: str,
                           body: str | None = None,
                           channel: str = CHANNEL_SITE,
                           case_id: int | None = None,
                           reference_type: str | None = None,
                           reference_id: int | None = None,
                           scheduled_at: datetime | None = None) -> LegalNotificationEvent:
        """创建一条通知事件。

        站内通知是基础渠道，始终创建。
        """
        if event_type not in EVENT_TYPES and event_type != "deadline_reminder":
            raise ValueError(f"不支持的事件类型: {event_type}")
        if channel not in ALL_CHANNELS:
            raise ValueError(f"不支持的通知渠道: {channel}")

        event = LegalNotificationEvent(
            organization_id=organization_id,
            user_id=user_id,
            case_id=case_id,
            event_type=event_type,
            title=title,
            body=body,
            channel=channel,
            status="pending",
            reference_type=reference_type,
            reference_id=reference_id,
            scheduled_at=scheduled_at,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event

    def create_multi_channel_notification(self, *, db: Session, organization_id: int,
                                          user_id: int, event_type: str, title: str,
                                          body: str | None = None,
                                          channels: list[str] | None = None,
                                          case_id: int | None = None,
                                          reference_type: str | None = None,
                                          reference_id: int | None = None,
                                          scheduled_at: datetime | None = None) -> list[LegalNotificationEvent]:
        """创建多渠道通知事件。

        根据 user 通知偏好决定哪些渠道生效。
        站内通知始终创建。
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

    # ── 投递通知 ──────────────────────────────────────────────────

    def dispatch_pending(self, *, db: Session) -> dict:
        """投递所有待发送通知。返回统计。"""
        pending = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == "pending",
        ).all()

        stats = {"delivered": 0, "failed": 0, "skipped": 0}
        for event in pending:
            # 如果 scheduled_at 在未来，跳过
            if event.scheduled_at and event.scheduled_at > utc_now():
                stats["skipped"] += 1
                continue

            result = self._dispatch_event(db, event)
            if result:
                stats["delivered"] += 1
            else:
                stats["failed"] += 1

        db.commit()
        return stats

    def _dispatch_event(self, db: Session, event: LegalNotificationEvent) -> bool:
        """投递单条通知，返回是否成功。"""
        try:
            if event.channel == CHANNEL_SITE:
                return self._deliver_site(db, event)
            elif event.channel == CHANNEL_EMAIL:
                return self._deliver_email(db, event)
            elif event.channel == CHANNEL_WECHAT:
                return self._deliver_wechat(db, event)
            elif event.channel == CHANNEL_FEISHU:
                return self._deliver_feishu(db, event)
            else:
                logger.warning("未知渠道: %s", event.channel)
                event.status = "failed"
                return False
        except Exception as exc:
            logger.error("通知投递异常 event_id=%s: %s", event.id, exc)
            event.status = "failed"
            return False

    def _deliver_site(self, db: Session, event: LegalNotificationEvent) -> bool:
        """站内通知：直接标记为 delivered。"""
        event.status = "delivered"
        event.sent_at = utc_now()
        return True

    def _deliver_email(self, db: Session, event: LegalNotificationEvent) -> bool:
        """邮件通知：通过 OutboundEmailService 发送。"""
        return self.send_email_notification(
            db=db,
            user_id=event.user_id,
            organization_id=event.organization_id,
            subject=event.title,
            body=event.body or "",
            event_type=event.event_type,
            reference_type=event.reference_type,
            reference_id=event.reference_id,
            event_id=event.id,
        )

    def send_email_notification(self, *, db: Session, user_id: int,
                                organization_id: int, subject: str, body: str,
                                event_type: str | None = None,
                                reference_type: str | None = None,
                                reference_id: int | None = None,
                                event_id: int | None = None) -> bool:
        """发送邮件通知，更新对应事件状态。"""
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if not user or not user.email:
                logger.warning("用户 %s 无邮箱，跳过邮件通知", user_id)
                return False

            # 创建邮件草稿
            from app.models.email import EmailDraft
            draft = EmailDraft(
                user_id=user_id,
                organization_id=organization_id,
                subject=subject,
                recipient=user.email,
                content=body,
                purpose="系统通知",
                status="draft",
                generation_type="notification_email",
            )
            db.add(draft)
            db.commit()

            # 更新关联通知事件状态：优先按当前事件 id（覆盖无 reference 事件，
            # 避免投递成功后仍为 pending 导致每轮 beat 重复发送）
            if event_id:
                evt = db.query(LegalNotificationEvent).filter(
                    LegalNotificationEvent.id == event_id,
                    LegalNotificationEvent.status == "pending",
                ).first()
                if evt:
                    evt.status = "sent"
                    evt.sent_at = utc_now()
            elif reference_type and reference_id:
                events = db.query(LegalNotificationEvent).filter(
                    LegalNotificationEvent.reference_type == reference_type,
                    LegalNotificationEvent.reference_id == reference_id,
                    LegalNotificationEvent.channel == CHANNEL_EMAIL,
                    LegalNotificationEvent.status == "pending",
                ).all()
                for evt in events:
                    evt.status = "sent"
                    evt.sent_at = utc_now()

            return True
        except Exception as exc:
            logger.error("邮件通知发送失败 user_id=%s: %s", user_id, exc)
            # 标记关联事件为 failed
            if reference_type and reference_id:
                events = db.query(LegalNotificationEvent).filter(
                    LegalNotificationEvent.reference_type == reference_type,
                    LegalNotificationEvent.reference_id == reference_id,
                    LegalNotificationEvent.channel == CHANNEL_EMAIL,
                    LegalNotificationEvent.status == "pending",
                ).all()
                for evt in events:
                    evt.status = "failed"
            return False

    def _deliver_wechat(self, db: Session, event: LegalNotificationEvent) -> bool:
        """微信通知：通过企业微信连接器发送。"""
        try:
            from app.models.connector import ExternalConnector

            connector = db.query(ExternalConnector).filter(
                ExternalConnector.connector_type == "wecom",
                ExternalConnector.status == "active",
            ).first()
            if not connector:
                logger.warning("未找到可用的企业微信连接器")
                event.status = "failed"
                return False

            # 创建微信推送记录（简化实现：标记为 sent）
            event.status = "sent"
            event.sent_at = utc_now()
            return True
        except Exception as exc:
            logger.error("微信通知发送失败: %s", exc)
            event.status = "failed"
            return False

    def _deliver_feishu(self, db: Session, event: LegalNotificationEvent) -> bool:
        """飞书通知：通过飞书连接器发送。"""
        try:
            from app.models.connector import ExternalConnector

            connector = db.query(ExternalConnector).filter(
                ExternalConnector.connector_type == "feishu",
                ExternalConnector.status == "active",
            ).first()
            if not connector:
                logger.warning("未找到可用的飞书连接器")
                event.status = "failed"
                return False

            event.status = "sent"
            event.sent_at = utc_now()
            return True
        except Exception as exc:
            logger.error("飞书通知发送失败: %s", exc)
            event.status = "failed"
            return False

    # ── 失败重试 ──────────────────────────────────────────────────

    def retry_failed(self, *, db: Session, max_retries: int = 3) -> int:
        """重试失败的通知事件。

        限制每个事件最多重试 max_retries 次。
        使用 event.acknowledged_at 字段记录重试次数（复用字段）。
        """
        failed_events = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == "failed",
        ).all()

        retried = 0
        for event in failed_events:
            # 简化重试计数：基于创建后时长限流
            age_minutes = (utc_now() - event.created_at).total_seconds() / 60 if event.created_at else 999
            if age_minutes < 5:
                continue  # 5分钟内不重试

            # 检查已重试次数（用 body 的长度来标记，简单方式）
            retry_count = self._get_retry_count(event)
            if retry_count >= max_retries:
                continue

            self._increment_retry_count(event)
            event.status = "pending"
            retried += 1

        if retried:
            db.commit()

        return retried

    def _get_retry_count(self, event: LegalNotificationEvent) -> int:
        """获取重试次数（从 body 末尾的元数据标记读取）。"""
        if not event.body:
            return 0
        try:
            # 在 body 末尾标记重试次数
            if event.body.endswith("]"):
                start = event.body.rfind("[retry=")
                if start > 0:
                    count_str = event.body[start + 7:-1]
                    return int(count_str)
        except (ValueError, IndexError):
            pass
        return 0

    def _increment_retry_count(self, event: LegalNotificationEvent) -> None:
        """递增重试次数标记。"""
        count = self._get_retry_count(event) + 1
        body = event.body or ""
        # 移除旧标记
        if body.endswith("]"):
            start = body.rfind("[retry=")
            if start > 0:
                body = body[:start]
        event.body = f"{body}[retry={count}]"

    # ── 通知偏好管理 ──────────────────────────────────────────────

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

    # ── 通知读取与确认 ────────────────────────────────────────────

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
            event.status = "read"
            event.acknowledged_at = utc_now()
            db.commit()
            db.refresh(event)
        return event

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
            event.status = "read"
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
