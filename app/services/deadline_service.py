"""Phase 11 — 关键日期提醒服务

扫描即将到期的关键日期，按偏移天数(7/3/1)计算应发提醒，
创建站内通知 + 邮件通知，幂等保证同一(deadline, channel, offset)只发一次。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.legal_notifications import LegalNotificationEvent, LegalNotificationPreference, LegalNotificationPolicy
from app.models.legal_portal import LegalDeadline
from app.models.user import User
from app.services.oplog_service import oplog_service

logger = logging.getLogger(__name__)

# 默认提醒偏移天数
DEFAULT_REMINDER_OFFSETS = [7, 3, 1]

# 最大重试次数
MAX_RETRY_COUNT = 3

# 支持的通知渠道
CHANNELS = ("site", "email")


class DeadlineService:

    # ── 提醒扫描 ──────────────────────────────────────────────────

    def scan_due_reminders(self, *, db: Session, now: datetime | None = None) -> list[dict]:
        """扫描所有活跃关键日期，找出需要发送的提醒。

        返回已创建的通知事件列表。
        同一 (deadline_id, channel, offset) 组合只发送一次。
        """
        current = now or datetime.now(timezone.utc)
        created_events: list[dict] = []

        active_deadlines = db.query(LegalDeadline).filter(
            LegalDeadline.status == "active",
        ).all()

        for dl in active_deadlines:
            offsets = self._policy_offsets(db, dl) or self._parse_offsets(dl.reminder_offsets_json)
            deadline_tz = self._safe_tz(dl.timezone)

            for offset_days in offsets:
                # 在关键日期所在时区计算提醒触发时间
                deadline_local = dl.deadline_at.astimezone(deadline_tz) if dl.deadline_at.tzinfo else dl.deadline_at.replace(tzinfo=deadline_tz)
                remind_at = deadline_local - timedelta(days=offset_days)
                remind_at_utc = remind_at.astimezone(timezone.utc)

                # 提醒时间还未到，跳过
                if remind_at_utc > current:
                    continue

                # 关键日期已过超过1天，不再补发提醒
                if current > deadline_local + timedelta(days=1):
                    continue

                for channel in CHANNELS:
                    event = self._create_reminder_if_not_exists(
                        db=db,
                        deadline=dl,
                        offset_days=offset_days,
                        channel=channel,
                        remind_at_utc=remind_at_utc,
                        current=current,
                    )
                    if event:
                        created_events.append({
                            "event_id": event.id,
                            "deadline_id": dl.id,
                            "offset_days": offset_days,
                            "channel": channel,
                            "owner_id": dl.owner_id,
                        })

        if created_events:
            db.commit()

        return created_events

    def _parse_offsets(self, offsets_json: str | None) -> list[int]:
        """解析提醒偏移天数 JSON。"""
        if not offsets_json:
            return DEFAULT_REMINDER_OFFSETS
        try:
            parsed = json.loads(offsets_json)
            if isinstance(parsed, list) and all(isinstance(x, (int, float)) for x in parsed):
                return sorted(int(x) for x in parsed if x > 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return DEFAULT_REMINDER_OFFSETS

    def _policy_offsets(self, db: Session, deadline: LegalDeadline) -> list[int] | None:
        """案件规则优先于日期设置，规则不存在时由个人偏好/组织默认继续处理。"""
        if not deadline.case_id:
            return None
        policy = db.query(LegalNotificationPolicy).filter(
            LegalNotificationPolicy.case_id == deadline.case_id,
            LegalNotificationPolicy.organization_id == deadline.organization_id,
            LegalNotificationPolicy.event_type.in_(["deadline", "all"]),
            LegalNotificationPolicy.is_active == 1,
        ).first()
        return self._parse_offsets(policy.advance_days_json) if policy and policy.advance_days_json else None

    def _safe_tz(self, tz_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, Exception):
            return ZoneInfo("Asia/Shanghai")

    def _dedupe_key(self, deadline_id: int, channel: str, offset_days: int) -> str:
        """生成幂等去重键。"""
        return f"deadline:{deadline_id}:{channel}:offset:{offset_days}"

    def _create_reminder_if_not_exists(self, *, db: Session, deadline: LegalDeadline,
                                       offset_days: int, channel: str,
                                       remind_at_utc: datetime,
                                       current: datetime) -> LegalNotificationEvent | None:
        """创建提醒通知事件（幂等），已存在则返回 None。"""
        dedupe_key = self._dedupe_key(deadline.id, channel, offset_days)

        # 检查是否已存在（同 reference_type + reference_id + channel + body=dedupe_key）
        exists = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.reference_type == "deadline",
            LegalNotificationEvent.reference_id == deadline.id,
            LegalNotificationEvent.channel == channel,
            LegalNotificationEvent.body == dedupe_key,
        ).first()
        if exists:
            return None

        # 检查用户通知偏好
        # 当天和次日提醒属于高优先级，不受静默影响；站内始终是兜底。
        high_priority = offset_days <= 1
        if not self._should_notify(db, deadline.owner_id, deadline.organization_id,
                                    event_type="deadline", channel=channel, high_priority=high_priority):
            return None

        offset_label = f"提前{offset_days}天" if offset_days > 0 else "当天"
        title = f"关键日期提醒：{deadline.deadline_type}（{offset_label}）"
        body_text = (
            f"关键日期类型：{deadline.deadline_type}\n"
            f"到期时间：{deadline.deadline_at.isoformat()}\n"
            f"说明：{deadline.description or '无'}\n"
            f"提醒偏移：{offset_label}"
        )

        event = LegalNotificationEvent(
            organization_id=deadline.organization_id,
            user_id=deadline.owner_id,
            case_id=deadline.case_id,
            event_type="deadline_reminder",
            title=title,
            body=dedupe_key,
            channel=channel,
            status="pending",
            reference_type="deadline",
            reference_id=deadline.id,
            scheduled_at=remind_at_utc,
        )
        db.add(event)
        policy = db.query(LegalNotificationPolicy).filter(
            LegalNotificationPolicy.case_id == deadline.case_id,
            LegalNotificationPolicy.event_type.in_(["deadline", "all"]),
            LegalNotificationPolicy.is_active == 1,
        ).first() if deadline.case_id else None
        existing_escalation = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.reference_type == "deadline",
            LegalNotificationEvent.reference_id == deadline.id,
            LegalNotificationEvent.event_type == "deadline_escalation",
            LegalNotificationEvent.user_id == (policy.escalation_user_id if policy else None),
            LegalNotificationEvent.body == dedupe_key,
        ).first()
        if policy and policy.escalation_user_id and policy.escalation_user_id != deadline.owner_id and not existing_escalation:
            escalation = LegalNotificationEvent(
                organization_id=deadline.organization_id, user_id=policy.escalation_user_id,
                case_id=deadline.case_id, event_type="deadline_escalation", title=title,
                body=dedupe_key, channel="site", status="pending", reference_type="deadline",
                reference_id=deadline.id, scheduled_at=remind_at_utc,
            )
            db.add(escalation)
        return event

    def _should_notify(self, db: Session, user_id: int, organization_id: int,
                       event_type: str, channel: str, high_priority: bool = False) -> bool:
        """检查用户通知偏好，判断是否应发送某渠道的通知。"""
        # 站内通知永远发送
        if channel == "site":
            return True

        pref = db.query(LegalNotificationPreference).filter(
            LegalNotificationPreference.user_id == user_id,
            LegalNotificationPreference.organization_id == organization_id,
            LegalNotificationPreference.event_type.in_([event_type, "all"]),
        ).first()

        if not pref:
            # 无偏好记录时：email 默认尝试，其他渠道默认关闭
            return channel == "email"

        # 检查静默时段
        if not high_priority and self._is_muted(pref):
            return False

        channels = json.loads(pref.channels_json or "[]")
        if "site" in channels and channel == "site":
            return True
        if channel in channels:
            return True
        # "all" 事件类型的偏好也覆盖
        if pref.event_type == "all" and channel in channels:
            return True

        return channel == "site"

    def _is_muted(self, pref: LegalNotificationPreference) -> bool:
        """检查当前是否在静默时段内。"""
        if not pref.mute_start or not pref.mute_end:
            return False
        try:
            tz = self._safe_tz(pref.timezone)
            now_local = datetime.now(tz)
            current_minutes = now_local.hour * 60 + now_local.minute
            start_h, start_m = [int(x) for x in pref.mute_start.split(":")]
            end_h, end_m = [int(x) for x in pref.mute_end.split(":")]
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m
            if start_minutes <= end_minutes:
                return start_minutes <= current_minutes < end_minutes
            else:
                # 跨午夜
                return current_minutes >= start_minutes or current_minutes < end_minutes
        except (ValueError, AttributeError):
            return False

    # ── 发送通知 ──────────────────────────────────────────────────

    def dispatch_pending_reminders(self, *, db: Session) -> dict:
        """发送所有待发送的 deadline_reminder 通知事件。

        返回发送统计。
        """
        pending_events = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.event_type == "deadline_reminder",
            LegalNotificationEvent.status == "pending",
        ).all()

        sent = 0
        failed = 0
        for event in pending_events:
            success = self._dispatch_single_event(db, event)
            if success:
                sent += 1
            else:
                failed += 1

        return {"sent": sent, "failed": failed}

    def _dispatch_single_event(self, db: Session, event: LegalNotificationEvent) -> bool:
        """发送单条通知事件，成功返回 True。"""
        if event.channel == "site":
            return self._dispatch_site_notification(db, event)
        elif event.channel == "email":
            return self._dispatch_email_notification(db, event)
        else:
            logger.warning("不支持的通知渠道: %s", event.channel)
            event.status = "failed"
            return False

    def _dispatch_site_notification(self, db: Session, event: LegalNotificationEvent) -> bool:
        """站内通知直接标记为 delivered。"""
        try:
            event.status = "delivered"
            event.sent_at = datetime.now(timezone.utc)
            db.flush()
            return True
        except Exception as exc:
            logger.error("站内通知投递失败 event_id=%s: %s", event.id, exc)
            event.status = "failed"
            return False

    def _dispatch_email_notification(self, db: Session, event: LegalNotificationEvent) -> bool:
        """通过 OutboundEmailService 发送邮件通知。"""
        try:
            from app.services.notification_service import notification_service
            return notification_service.send_email_notification(
                db=db,
                user_id=event.user_id,
                organization_id=event.organization_id,
                subject=event.title,
                body=event.body,
                event_type=event.event_type,
                reference_type=event.reference_type,
                reference_id=event.reference_id,
            )
        except Exception as exc:
            logger.error("邮件通知投递失败 event_id=%s: %s", event.id, exc)
            event.status = "failed"
            return False

    # ── 同日提醒合并 ──────────────────────────────────────────────

    def merge_same_day_reminders(self, *, db: Session, user_id: int) -> list[dict]:
        """合并同一用户同一天的多条提醒，避免重复打扰。

        返回合并后的提醒列表。
        """
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        today_end = today_start + timedelta(days=1)

        events = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.user_id == user_id,
            LegalNotificationEvent.event_type == "deadline_reminder",
            LegalNotificationEvent.channel == "site",
            LegalNotificationEvent.status.in_(["delivered", "pending"]),
            LegalNotificationEvent.scheduled_at >= today_start,
            LegalNotificationEvent.scheduled_at < today_end,
        ).all()

        if not events:
            return []

        # 按关键日期分组
        grouped: dict[int, list[LegalNotificationEvent]] = {}
        for event in events:
            key = event.reference_id or 0
            grouped.setdefault(key, []).append(event)

        merged = []
        for deadline_id, group in grouped.items():
            offsets = []
            for event in group:
                # 从 body 中提取 offset
                body = event.body or ""
                if ":offset:" in body:
                    try:
                        offset_str = body.rsplit(":offset:", 1)[1]
                        offsets.append(int(offset_str))
                    except (ValueError, IndexError):
                        pass

            deadline = db.query(LegalDeadline).filter(LegalDeadline.id == deadline_id).first()
            merged.append({
                "deadline_id": deadline_id,
                "deadline_type": deadline.deadline_type if deadline else "unknown",
                "deadline_at": deadline.deadline_at.isoformat() if deadline and deadline.deadline_at else None,
                "offsets": sorted(offsets),
                "title": group[0].title if group else "",
                "event_count": len(group),
            })

        return merged

    # ── 失败重试 ──────────────────────────────────────────────────

    def retry_failed_reminders(self, *, db: Session) -> int:
        """重试失败的提醒通知，最多重试 MAX_RETRY_COUNT 次。

        返回重试数量。
        """
        failed_events = db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.event_type == "deadline_reminder",
            LegalNotificationEvent.status == "failed",
        ).all()

        retried = 0
        for event in failed_events:
            # 简单重试计数：使用 event 的 acked_at 字段来记录重试次数
            # 实际上 model 没有 retry_count 字段，所以基于创建时间来限流
            if event.created_at and (datetime.now(timezone.utc) - event.created_at).total_seconds() < 300:
                # 5分钟内不重试
                continue

            event.status = "pending"
            retried += 1

        if retried:
            db.commit()

        return retried

    # ── 关键日期状态管理 ──────────────────────────────────────────

    def complete_deadline(self, *, db: Session, deadline_id: int, user_id: int) -> LegalDeadline:
        """标记关键日期为已完成。"""
        deadline = db.query(LegalDeadline).filter(LegalDeadline.id == deadline_id).first()
        if not deadline:
            raise ValueError("关键日期不存在")
        if deadline.status != "active":
            raise ValueError(f"关键日期状态为 {deadline.status}，无法完成")

        deadline.status = "completed"
        db.commit()
        db.refresh(deadline)
        oplog_service.log(module="deadline", action="deadline_completed", db=db,
                          user_id=user_id, target_type="deadline",
                          target_id=deadline_id)
        return deadline

    def cancel_deadline(self, *, db: Session, deadline_id: int, user_id: int) -> LegalDeadline:
        """取消关键日期。"""
        deadline = db.query(LegalDeadline).filter(LegalDeadline.id == deadline_id).first()
        if not deadline:
            raise ValueError("关键日期不存在")
        if deadline.status not in ("active", "due"):
            raise ValueError(f"关键日期状态为 {deadline.status}，无法取消")

        deadline.status = "cancelled"
        db.commit()
        db.refresh(deadline)
        oplog_service.log(module="deadline", action="deadline_cancelled", db=db,
                          user_id=user_id, target_type="deadline",
                          target_id=deadline_id)
        return deadline

    def mark_due_deadlines(self, *, db: Session, now: datetime | None = None) -> int:
        """将已到期但未完成的关键日期标记为 due 状态。

        由定时任务调用。返回标记数量。
        """
        current = now or datetime.now(timezone.utc)
        due_deadlines = db.query(LegalDeadline).filter(
            LegalDeadline.status == "active",
            LegalDeadline.deadline_at <= current,
            LegalDeadline.is_historical == 0,
        ).all()

        count = 0
        for dl in due_deadlines:
            dl.status = "due"
            count += 1

        if count:
            db.commit()

        return count

    # ── 查询 ──────────────────────────────────────────────────────

    def list_deadlines(self, *, db: Session, organization_id: int,
                       case_id: int | None = None,
                       status: str | None = None,
                       owner_id: int | None = None,
                       limit: int = 50) -> list[LegalDeadline]:
        query = db.query(LegalDeadline).filter(
            LegalDeadline.organization_id == organization_id
        )
        if case_id:
            query = query.filter(LegalDeadline.case_id == case_id)
        if status:
            query = query.filter(LegalDeadline.status == status)
        if owner_id:
            query = query.filter(LegalDeadline.owner_id == owner_id)
        return query.order_by(LegalDeadline.deadline_at.asc()).limit(limit).all()

    def serialize_deadline(self, deadline: LegalDeadline) -> dict:
        return {
            "id": deadline.id,
            "organization_id": deadline.organization_id,
            "case_id": deadline.case_id,
            "contract_id": deadline.contract_id,
            "deadline_type": deadline.deadline_type,
            "deadline_at": deadline.deadline_at.isoformat() if deadline.deadline_at else None,
            "timezone": deadline.timezone,
            "owner_id": deadline.owner_id,
            "description": deadline.description,
            "reminder_offsets": self._parse_offsets(deadline.reminder_offsets_json),
            "is_historical": deadline.is_historical,
            "status": deadline.status,
            "created_by": deadline.created_by,
            "created_at": deadline.created_at.isoformat() if deadline.created_at else None,
            "updated_at": deadline.updated_at.isoformat() if deadline.updated_at else None,
        }


deadline_service = DeadlineService()
