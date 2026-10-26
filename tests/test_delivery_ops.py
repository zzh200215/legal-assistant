"""Outbox 投递看板：窗口投递率、当前积压、死信明细与人工重排。

两类 Outbox（通知事件 / 邮件请求）分别覆盖：口径区分「窗口内」与「当前积压」，
死信重排只重置投递列，越权与非法状态给稳定错误。
"""
import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.core.time import utc_now
from app.models.email import EmailSendRequest
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User, UserStatus
from app.services.notification.delivery_ops import (
    outbox_overview, retry_dead_letter,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _event(org_id, user_id, *, channel="email", status="pending", **kw):
    params = dict(
        organization_id=org_id, user_id=user_id, event_type="approval",
        title="审核即将到期：合同 A", body="sla:due_soon · 合同 #1",
        channel=channel, status=status, reference_type="contract_review",
        reference_id=8, template_key="review_sla_due_soon",
    )
    params.update(kw)
    return LegalNotificationEvent(**params)


class DeliveryOpsOverviewTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = Session()

        org = Organization(name="OpsOrg", code="OPS")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id

        self.admin = User(username="ops_admin", email="ops_admin@example.com",
                          hashed_password=hash_password("pw"), role="admin",
                          status=UserStatus.active.value, organization_id=org.id)
        self.other_admin = User(username="ops_admin2", email="ops_admin2@example.com",
                                hashed_password=hash_password("pw"), role="admin",
                                status=UserStatus.active.value, organization_id=None)
        self.db.add_all([self.admin, self.other_admin])
        self.db.flush()
        self.admin_id = self.admin.id
        self.db.commit()

        now = utc_now()
        self.db.add_all([
            # 站内：投递完成
            _event(self.org_id, self.admin_id, channel="site", status="delivered"),
            _event(self.org_id, self.admin_id, channel="site", status="read",
                   reference_id=9),
            # 邮件：成功 / 失败待重试 / 死信 / 积压
            _event(self.org_id, self.admin_id, status="sent", reference_id=10),
            _event(self.org_id, self.admin_id, status="failed", reference_id=11,
                   attempt=2, next_retry_at=now - timedelta(minutes=1),
                   error_code="SMTP_TIMEOUT"),
            _event(self.org_id, self.admin_id, status="dead_letter", reference_id=12,
                   attempt=5, error_code="TEMPLATE_ERROR"),
            _event(self.org_id, self.admin_id, status="sending", reference_id=13,
                   claim_expires_at=now - timedelta(minutes=10)),
        ])
        # 窗口外的一条（不应计入窗口统计，但仍在积压口径里）
        old = _event(self.org_id, self.admin_id, status="pending", reference_id=14)
        old.created_at = now - timedelta(days=30)
        self.db.add(old)
        self.db.commit()

        self.db.add_all([
            EmailSendRequest(
                draft_id=1, smtp_connector_id=1, user_id=self.admin_id,
                organization_id=self.org_id, recipient="client@example.com",
                subject="审核即将到期", content_hash="h1", idempotency_key="ops-1",
                status="sent", sent_at=now - timedelta(hours=2),
            ),
            EmailSendRequest(
                draft_id=2, smtp_connector_id=1, user_id=self.admin_id,
                organization_id=self.org_id, recipient="boss@corp.example.com",
                subject="咨询被退回", content_hash="h2", idempotency_key="ops-2",
                status="dead_letter", attempt=5, error_code="SMTP_AUTH",
                dead_letter_at=now - timedelta(minutes=3),
                dead_letter_reason="达到最大重试次数或不可恢复错误",
            ),
            EmailSendRequest(
                draft_id=3, smtp_connector_id=1, user_id=self.admin_id,
                organization_id=self.org_id, recipient="pending@example.com",
                subject="待审批", content_hash="h3", idempotency_key="ops-3",
                status="pending",
            ),
            EmailSendRequest(
                draft_id=4, smtp_connector_id=1, user_id=self.admin_id,
                organization_id=self.org_id, recipient="dlp@example.com",
                subject="命中 DLP", content_hash="h4", idempotency_key="ops-4",
                status="blocked",
            ),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_window_summary_counts_and_rate(self):
        data = outbox_overview(self.db, days=7)
        notification = data["notification"]
        # 窗口内 6 条（站点 2 + 邮件 4），窗口外那条不算
        self.assertEqual(notification["total"], 6)
        self.assertEqual(notification["success"], 3)       # delivered/read/sent
        self.assertEqual(notification["failed"], 1)
        self.assertEqual(notification["dead_letter"], 1)
        # 成功率 = 3 / (3 + 1 + 1)
        self.assertAlmostEqual(notification["rate"], 0.6, places=4)

        channels = {row["channel"]: row for row in notification["by_channel"]}
        self.assertEqual(channels["site"]["delivered"], 2)
        self.assertEqual(channels["site"]["rate"], 1.0)
        self.assertEqual(channels["email"]["dead_letter"], 1)

    def test_backlog_is_current_not_windowed(self):
        data = outbox_overview(self.db, days=7)
        backlog = data["notification"]["backlog"]
        # pending 1（窗口外那条）+ sending 1
        self.assertEqual(backlog["pending"], 1)
        self.assertEqual(backlog["sending"], 1)
        self.assertEqual(backlog["retry_pending"], 1)
        self.assertEqual(backlog["stale_claims"], 1)
        self.assertEqual(backlog["open_total"], 2)
        self.assertGreaterEqual(backlog["oldest_pending_age_minutes"], 30 * 24 * 60 - 5)

    def test_dead_letter_items_and_failure_codes(self):
        data = outbox_overview(self.db, days=7)
        dead = data["notification"]["dead_letters"]
        self.assertEqual(dead["total"], 1)
        item = dead["items"][0]
        self.assertEqual(item["error_code"], "TEMPLATE_ERROR")
        self.assertEqual(item["template_key"], "review_sla_due_soon")
        codes = {row["code"]: row["count"] for row in data["notification"]["failure_codes"]}
        self.assertEqual(codes["TEMPLATE_ERROR"], 1)
        self.assertEqual(codes["SMTP_TIMEOUT"], 1)

    def test_email_section_counts_and_recipient_masked(self):
        data = outbox_overview(self.db, days=7)
        email = data["email"]
        self.assertEqual(email["total"], 4)
        self.assertEqual(email["sent"], 1)
        self.assertEqual(email["dead_letter"], 1)
        self.assertEqual(email["dlp_blocked"], 1)
        self.assertEqual(email["sent_24h"], 1)
        self.assertEqual(email["backlog"]["pending"], 1)
        self.assertEqual(email["dead_letters"]["items"][0]["recipient"], "b***@corp.example.com")
        self.assertFalse(email["policy_enabled"], "未配置外发策略")

    def test_alerts_surface_dead_letters_and_stale_claims(self):
        data = outbox_overview(self.db, days=7)
        codes = {alert["code"] for alert in data["alerts"]}
        self.assertIn("DEAD_LETTER", codes)
        self.assertIn("STALE_CLAIMS", codes)
        # 到期待重试只有 1 条，低于默认阈值 10 → 不告警
        self.assertNotIn("RETRY_STUCK", codes)
        self.assertIn("EMAIL_POLICY_DISABLED", codes)
        # 结构化健康检查与 alerts 同源：阈值超限项一一对应
        breached = {check["code"] for check in data["health"]["breached"]}
        self.assertEqual(breached, {"DEAD_LETTER", "STALE_CLAIMS"})

    def test_health_thresholds_use_settings(self):
        from unittest.mock import patch

        from app.core.config import get_settings

        with patch.object(get_settings(), "OUTBOX_BACKLOG_WARN_THRESHOLD", 1):
            data = outbox_overview(self.db, days=7)
        breached = {check["code"] for check in data["health"]["breached"]}
        self.assertIn("NOTIFICATION_BACKLOG", breached)

    def test_trend_included_in_overview(self):
        data = outbox_overview(self.db, days=7)
        trend = data["trend"]
        self.assertEqual(trend["days"], 7)
        self.assertEqual(len(trend["notification"]), 7)
        self.assertEqual(len(trend["email"]), 7)
        # 今天创建了 6 条通知
        self.assertEqual(trend["notification"][-1]["created"], 6)

    def test_window_is_clamped(self):
        self.assertEqual(outbox_overview(self.db, days=0)["window"]["days"], 1)
        self.assertEqual(outbox_overview(self.db, days=999)["window"]["days"], 90)


class DeadLetterRetryTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = Session()

        org = Organization(name="RetryOrg", code="RTY")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id
        self.admin = User(username="retry_admin", email="retry_admin@example.com",
                          hashed_password=hash_password("pw"), role="admin",
                          status=UserStatus.active.value, organization_id=org.id)
        self.outsider = User(username="outsider", email="outsider@example.com",
                             hashed_password=hash_password("pw"), role="admin",
                             status=UserStatus.active.value, organization_id=None)
        self.db.add_all([self.admin, self.outsider])
        self.db.flush()
        self.admin_id = self.admin.id
        self.db.commit()

        self.event = _event(self.org_id, self.admin_id, status="dead_letter",
                            attempt=5, error_code="TEMPLATE_ERROR")
        self.db.add(self.event)
        self.db.commit()
        self.event_id = self.event.id

        self.request = EmailSendRequest(
            draft_id=1, smtp_connector_id=1, user_id=self.admin_id,
            organization_id=self.org_id, recipient="client@example.com",
            subject="死信邮件", content_hash="rh1", idempotency_key="retry-1",
            status="dead_letter", attempt=5, error_code="SMTP_AUTH",
            dead_letter_at=utc_now(),
        )
        self.db.add(self.request)
        self.db.commit()
        self.request_id = self.request.id

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_notification_dead_letter_requeued(self):
        result = retry_dead_letter(self.db, kind="notification", item_id=self.event_id,
                                   user=self.admin)
        self.assertEqual(result["status"], "pending")
        event = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.id == self.event_id).first()
        self.assertEqual(event.attempt, 0)
        self.assertIsNone(event.error_code)
        self.assertIsNone(event.next_retry_at)

    def test_email_dead_letter_requeued_to_pending_approval(self):
        result = retry_dead_letter(self.db, kind="email", item_id=self.request_id, user=self.admin)
        self.assertEqual(result["status"], "pending")
        request = self.db.query(EmailSendRequest).filter(
            EmailSendRequest.id == self.request_id).first()
        self.assertEqual(request.attempt, 0)
        self.assertIsNone(request.dead_letter_at)

    def test_non_dead_letter_cannot_be_requeued(self):
        self.event.status = "delivered"
        self.db.commit()
        with self.assertRaises(ValueError):
            retry_dead_letter(self.db, kind="notification", item_id=self.event_id, user=self.admin)

    def test_permission_denied_for_other_organization(self):
        self.outsider.role = "user"
        self.db.commit()
        with self.assertRaises(PermissionError):
            retry_dead_letter(self.db, kind="notification", item_id=self.event_id, user=self.outsider)

    def test_missing_item_and_unknown_kind(self):
        with self.assertRaises(LookupError):
            retry_dead_letter(self.db, kind="notification", item_id=999999, user=self.admin)
        with self.assertRaises(ValueError):
            retry_dead_letter(self.db, kind="webhook", item_id=1, user=self.admin)


if __name__ == "__main__":
    unittest.main()
