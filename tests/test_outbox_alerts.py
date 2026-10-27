"""Outbox 健康检查与告警：阈值口径、按组织告警、冷却/升级、静默时段、每日趋势分桶。"""
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.config import get_settings
from app.core.database import Base
from app.core.time import utc_now
from app.models.email import EmailSendRequest
from app.models.legal_notifications import (
    LegalNotificationEvent, LegalNotificationPreference, OutboxAlertState,
)
from app.models.org import Organization
from app.models.user import User, UserStatus
from app.services.notification.delivery_ops import daily_trend, evaluate_health, outbox_overview
from app.services.notification.notification_service import notification_service
from app.services.notification.outbox_alerts import (
    ESCALATION_TEMPLATE_KEY, TEMPLATE_KEY, run_outbox_health_check,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _event(org_id, user_id, *, status="pending", channel="site", **kw):
    params = dict(
        organization_id=org_id, user_id=user_id, event_type="approval",
        title="审核即将到期：合同 A", body="sla:due_soon", channel=channel, status=status,
    )
    params.update(kw)
    return LegalNotificationEvent(**params)


class OutboxHealthTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()

        org = Organization(name="HealthOrg", code="HLTH")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id

        self.admin = User(username="health_admin", email="health_admin@example.com",
                          hashed_password=hash_password("pw"), role="admin",
                          status=UserStatus.active.value, organization_id=org.id)
        self.member = User(username="health_member", email="health_member@example.com",
                           hashed_password=hash_password("pw"), role="user",
                           status=UserStatus.active.value, organization_id=org.id)
        self.db.add_all([self.admin, self.member])
        self.db.flush()
        self.admin_id = self.admin.id
        self.db.commit()

        now = utc_now()
        self.db.add_all([
            _event(self.org_id, self.admin_id, status="pending", channel="email"),
            _event(self.org_id, self.admin_id, status="pending", channel="email"),
            _event(self.org_id, self.admin_id, status="sending", channel="email",
                   claim_expires_at=now - timedelta(minutes=10)),
            _event(self.org_id, self.admin_id, status="failed", channel="email",
                   next_retry_at=now - timedelta(minutes=5), attempt=2),
            _event(self.org_id, self.admin_id, status="dead_letter", channel="email",
                   attempt=5, error_code="TEMPLATE_ERROR"),
        ])
        self.db.add(EmailSendRequest(
            draft_id=1, smtp_connector_id=1, user_id=self.admin_id, organization_id=self.org_id,
            recipient="client@example.com", subject="死信邮件", content_hash="h1",
            idempotency_key="health-1", status="dead_letter", attempt=5, error_code="SMTP_AUTH",
            dead_letter_at=now,
        ))
        self.db.add(EmailSendRequest(
            draft_id=2, smtp_connector_id=1, user_id=self.admin_id, organization_id=self.org_id,
            recipient="pending@example.com", subject="待审批", content_hash="h2",
            idempotency_key="health-2", status="pending",
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_evaluate_health_default_thresholds(self):
        health = evaluate_health(self.db)
        checks = {item["code"]: item for item in health["checks"]}
        self.assertEqual(checks["NOTIFICATION_BACKLOG"]["value"], 3, "pending 2 + sending 1")
        self.assertEqual(checks["EMAIL_BACKLOG"]["value"], 1)
        self.assertEqual(checks["DEAD_LETTER"]["value"], 2)
        self.assertEqual(checks["STALE_CLAIMS"]["value"], 1)
        self.assertEqual(checks["RETRY_STUCK"]["value"], 1)
        breached = {item["code"] for item in health["breached"]}
        self.assertEqual(breached, {"DEAD_LETTER", "STALE_CLAIMS"})
        self.assertFalse(health["healthy"])

    def test_thresholds_are_configurable(self):
        with patch.object(get_settings(), "OUTBOX_BACKLOG_WARN_THRESHOLD", 3):
            health = evaluate_health(self.db, organization_id=self.org_id)
        breached = {item["code"] for item in health["breached"]}
        self.assertIn("NOTIFICATION_BACKLOG", breached)

    def test_healthy_when_below_thresholds(self):
        self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status.in_(("pending", "sending", "dead_letter"))).delete()
        self.db.query(EmailSendRequest).delete()
        self.db.commit()
        health = evaluate_health(self.db)
        self.assertTrue(health["healthy"])
        self.assertEqual(health["breached"], [])

    def test_daily_trend_fills_empty_days(self):
        now = utc_now()
        trend = daily_trend(self.db, days=7, now=now)
        self.assertEqual(len(trend["notification"]), 7)
        self.assertEqual(len(trend["email"]), 7)
        self.assertEqual(trend["notification"][-1]["date"], now.date().isoformat())
        today = trend["notification"][-1]
        self.assertEqual(today["created"], 5)
        self.assertEqual(today["dead_letter"], 1)
        self.assertEqual(today["open"], 3)
        # 空白天补 0
        self.assertEqual(trend["notification"][0]["created"], 0)

    def test_trend_caps_buckets(self):
        trend = daily_trend(self.db, days=365)
        self.assertEqual(trend["days"], 30)
        self.assertEqual(len(trend["notification"]), 30)

    def test_overview_contains_health_and_trend(self):
        data = outbox_overview(self.db, days=7)
        self.assertIn("trend", data)
        self.assertIn("health", data)
        codes = {alert["code"] for alert in data["alerts"]}
        self.assertIn("DEAD_LETTER", codes)


class OutboxAlertTaskTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()

        org = Organization(name="AlertOrg", code="ALRT")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id
        self.admin = User(username="alert_admin", email="alert_admin@example.com",
                          hashed_password=hash_password("pw"), role="admin",
                          status=UserStatus.active.value, organization_id=self.org_id)
        self.other_admin = User(username="alert_admin2", email="alert_admin2@example.com",
                                hashed_password=hash_password("pw"), role="admin",
                                status=UserStatus.active.value, organization_id=self.org_id)
        self.member = User(username="alert_member", email="alert_member@example.com",
                           hashed_password=hash_password("pw"), role="user",
                           status=UserStatus.active.value, organization_id=self.org_id)
        self.platform_admin = User(username="platform", email="platform@example.com",
                                   hashed_password=hash_password("pw"), role="admin",
                                   status=UserStatus.active.value, organization_id=None)
        self.db.add_all([self.admin, self.other_admin, self.member, self.platform_admin])
        self.db.commit()
        self.admin_ids = {self.admin.id, self.other_admin.id}

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _breach(self):
        now = utc_now()
        self.db.add(_event(self.org_id, self.admin.id, status="dead_letter", channel="email",
                           attempt=5, error_code="TEMPLATE_ERROR"))
        self.db.commit()
        return now

    def _alerts(self):
        return self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.template_key == TEMPLATE_KEY).all()

    def _escalations(self):
        return self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.template_key == ESCALATION_TEMPLATE_KEY).all()

    def _enable_email(self):
        from app.models.email import OutboundEmailPolicy

        self.db.add(OutboundEmailPolicy(organization_id=self.org_id, enabled=True,
                                        allowed_recipient_domains_json='["example.com"]'))
        self.db.commit()

    def _quiet_pref(self, user, *, start="00:00", end="23:59"):
        from app.models.legal_notifications import LegalNotificationPreference

        self.db.add(LegalNotificationPreference(
            user_id=user.id, organization_id=self.org_id, event_type="approval",
            channels_json='["site", "email"]', mute_start=start, mute_end=end,
            timezone="Asia/Shanghai",
        ))
        self.db.commit()

    def test_no_alert_when_healthy(self):
        result = run_outbox_health_check(self.db)
        self.assertEqual(result["breached_orgs"], 0)
        self.assertEqual(self._alerts(), [])

    def test_state_tracks_streak_and_resolution(self):
        now = self._breach()
        run_outbox_health_check(self.db, now=now)
        state = self.db.query(OutboxAlertState).first()
        self.assertIsNotNone(state)
        self.assertEqual(state.breach_streak, 1)
        self.assertIsNotNone(state.first_breached_at)

        run_outbox_health_check(self.db, now=now + timedelta(minutes=15))
        self.db.refresh(state)
        self.assertEqual(state.breach_streak, 2)

        # 投递恢复：状态清零并记 resolved_at
        self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == "dead_letter").delete()
        self.db.commit()
        result = run_outbox_health_check(self.db, now=now + timedelta(minutes=30))
        self.db.refresh(state)
        self.assertEqual(result["resolved_orgs"], 1)
        self.assertEqual(state.breach_streak, 0)
        self.assertIsNone(state.first_breached_at)
        self.assertIsNotNone(state.resolved_at)

    def test_escalation_after_sustained_breach(self):
        now = self._breach()
        run_outbox_health_check(self.db, now=now)
        self.assertEqual(self._escalations(), [])

        # 持续 61 分钟（默认阈值 60）→ 每个管理员各一条升级（无邮件策略时只有站内）
        run_outbox_health_check(self.db, now=now + timedelta(minutes=61))
        escalations = self._escalations()
        self.assertEqual({event.user_id for event in escalations}, self.admin_ids)
        self.assertEqual(len(escalations), len(self.admin_ids))
        self.assertIn("升级", escalations[0].title)
        self.assertIn("已持续 61 分钟", escalations[0].title)

        # 升级冷却内重复扫描：不新增
        run_outbox_health_check(self.db, now=now + timedelta(minutes=75))
        self.assertEqual(len(self._escalations()), len(self.admin_ids))

    def test_quiet_hours_send_site_only(self):
        self._enable_email()
        self._quiet_pref(self.admin)
        now = self._breach()
        result = run_outbox_health_check(self.db, now=now)
        self.assertEqual(result["quiet_users"], 1)

        admin_events = [event for event in self._alerts() if event.user_id == self.admin.id]
        self.assertEqual([event.channel for event in admin_events], ["site"])
        self.assertIn("静默", admin_events[0].body)
        # 另一个没设偏好的管理员仍然双渠道
        other_events = [event for event in self._alerts() if event.user_id == self.other_admin.id]
        self.assertEqual({event.channel for event in other_events}, {"site", "email"})

    def test_escalation_bypasses_quiet_hours(self):
        self._enable_email()
        self._quiet_pref(self.admin)
        now = self._breach()
        run_outbox_health_check(self.db, now=now)
        run_outbox_health_check(self.db, now=now + timedelta(minutes=61))

        admin_escalations = [event for event in self._escalations() if event.user_id == self.admin.id]
        self.assertEqual({event.channel for event in admin_escalations}, {"site", "email"})
        self.assertNotIn("静默", admin_escalations[0].body)

    def test_quiet_hours_can_be_disabled(self):
        self._enable_email()
        self._quiet_pref(self.admin)
        now = self._breach()
        with patch.object(get_settings(), "OUTBOX_ALERT_RESPECT_QUIET_HOURS", False):
            run_outbox_health_check(self.db, now=now)
        admin_events = [event for event in self._alerts() if event.user_id == self.admin.id]
        self.assertEqual({event.channel for event in admin_events}, {"site", "email"})

    def test_alerts_org_admins_only(self):
        self._breach()
        result = run_outbox_health_check(self.db)
        self.assertEqual(result["checked_orgs"], 1)
        self.assertEqual(result["breached_orgs"], 1)
        events = self._alerts()
        self.assertEqual({event.user_id for event in events}, self.admin_ids)
        self.assertNotIn(self.member.id, {event.user_id for event in events})
        self.assertNotIn(self.platform_admin.id, {event.user_id for event in events})
        self.assertIn("投递告警", events[0].title)
        self.assertIn("死信", events[0].body)

    def test_alert_is_idempotent_within_cooldown(self):
        now = self._breach()
        # 冷却窗口是 180 分钟的绝对时间桶；若当前时刻距桶边界不足 15 分钟，
        # +15 分钟的第二次扫描会跨桶，导致「同一冷却窗口」的前提不成立。
        # 固定到 :30，保证 +15 分钟仍落在同一桶内（与真实墙钟无关）。
        now = now.replace(minute=30, second=0, microsecond=0)
        run_outbox_health_check(self.db, now=now)
        first = len(self._alerts())
        # 同一冷却窗口（+15 分钟）再扫一次：命中幂等键，不新增
        run_outbox_health_check(self.db, now=now + timedelta(minutes=15))
        self.assertEqual(len(self._alerts()), first)
        # 跨窗口（+ 冷却 + 1 分钟）：允许再发一次
        cooldown = get_settings().OUTBOX_ALERT_COOLDOWN_MINUTES
        run_outbox_health_check(self.db, now=now + timedelta(minutes=cooldown + 1))
        self.assertGreater(len(self._alerts()), first)

    def test_org_scope_isolated(self):
        other = Organization(name="OtherOrg", code="OTHR")
        self.db.add(other)
        self.db.flush()
        other_admin = User(username="other_admin", email="other_admin@example.com",
                           hashed_password=hash_password("pw"), role="admin",
                           status=UserStatus.active.value, organization_id=other.id)
        self.db.add(other_admin)
        self.db.commit()
        # 只有 alert_org 有死信
        self._breach()
        run_outbox_health_check(self.db)
        events = self._alerts()
        self.assertEqual({event.organization_id for event in events}, {self.org_id})

    def test_celery_task_runs_with_sessionlocal(self):
        from app.tasks import check_outbox_health_task

        self._breach()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        with patch("app.tasks.notification_tasks.SessionLocal", Session):
            result = check_outbox_health_task()
        self.assertEqual(result["breached_orgs"], 1)
        self.assertGreaterEqual(result["notified_users"], 2)

    def test_no_org_admin_is_reported_as_skipped(self):
        self.admin.organization_id = None
        self.other_admin.organization_id = None
        self.db.commit()
        result = run_outbox_health_check(self.db)
        self.assertEqual(result["skipped"], "no_org_admin")
        self.assertEqual(result["checked_orgs"], 0)
        self.assertEqual(self._alerts(), [])


class QuietHoursTests(unittest.TestCase):
    """免打扰窗口解析：跨夜、同时段、无偏好、非法时区。"""

    @staticmethod
    def _pref(start, end, timezone_name="Asia/Shanghai"):
        return LegalNotificationPreference(
            user_id=1, organization_id=1, event_type="approval", channels_json="[]",
            mute_start=start, mute_end=end, timezone=timezone_name,
        )

    # 2026-10-27 17:30 UTC == 2026-10-28 01:30 Asia/Shanghai
    NOW = datetime(2026, 10, 27, 17, 30)

    def test_overnight_window_is_quiet(self):
        self.assertTrue(notification_service.is_quiet_now(
            self._pref("22:00", "08:00"), now=self.NOW))

    def test_daytime_window_is_not_quiet_at_night(self):
        self.assertFalse(notification_service.is_quiet_now(
            self._pref("09:00", "18:00"), now=self.NOW))

    def test_same_start_end_disables_quiet(self):
        self.assertFalse(notification_service.is_quiet_now(
            self._pref("08:00", "08:00"), now=self.NOW))

    def test_missing_pref_or_fields_is_not_quiet(self):
        self.assertFalse(notification_service.is_quiet_now(None, now=self.NOW))
        self.assertFalse(notification_service.is_quiet_now(self._pref(None, None), now=self.NOW))

    def test_invalid_timezone_falls_back_to_utc(self):
        # 17:30 UTC 落在 16:00-18:00 窗口内（按 UTC 解释），不抛异常
        self.assertTrue(notification_service.is_quiet_now(
            self._pref("16:00", "18:00", timezone_name="Not/AZone"), now=self.NOW))


if __name__ == "__main__":
    unittest.main()
