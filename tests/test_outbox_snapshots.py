"""积压快照：采样字段、保留期清理、历史曲线与小时吞吐分桶。"""
import unittest
from datetime import timedelta
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
from app.models.legal_notifications import LegalNotificationEvent, OutboxSnapshot
from app.models.org import Organization
from app.models.user import User, UserStatus
from app.services.notification.outbox_snapshots import (
    capture_snapshots, history, prune_snapshots,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _event(org_id, user_id, *, status="pending", channel="email", **kw):
    params = dict(
        organization_id=org_id, user_id=user_id, event_type="approval",
        title="提醒", body="body", channel=channel, status=status,
    )
    params.update(kw)
    return LegalNotificationEvent(**params)


class OutboxSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()

        org = Organization(name="SnapOrg", code="SNAP")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id
        self.admin = User(username="snap_admin", email="snap_admin@example.com",
                          hashed_password=hash_password("pw"), role="admin",
                          status=UserStatus.active.value, organization_id=org.id)
        self.db.add(self.admin)
        self.db.commit()
        self.admin_id = self.admin.id

        now = utc_now()
        self.db.add_all([
            _event(self.org_id, self.admin_id, status="pending"),
            _event(self.org_id, self.admin_id, status="approved"),
            _event(self.org_id, self.admin_id, status="sending",
                   claim_expires_at=now - timedelta(minutes=10)),
            _event(self.org_id, self.admin_id, status="dead_letter", attempt=5,
                   error_code="TEMPLATE_ERROR"),
            _event(self.org_id, self.admin_id, status="failed", attempt=1,
                   next_retry_at=now - timedelta(minutes=1)),
            _event(self.org_id, self.admin_id, status="sent", sent_at=now),
        ])
        self.db.add(EmailSendRequest(
            draft_id=1, smtp_connector_id=1, user_id=self.admin_id, organization_id=self.org_id,
            recipient="a@example.com", subject="s", content_hash="h1",
            idempotency_key="snap-1", status="dead_letter", attempt=5, error_code="SMTP_AUTH",
            dead_letter_at=now,
        ))
        self.db.add(EmailSendRequest(
            draft_id=2, smtp_connector_id=1, user_id=self.admin_id, organization_id=self.org_id,
            recipient="b@example.com", subject="s", content_hash="h2",
            idempotency_key="snap-2", status="sent", sent_at=now,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_capture_writes_platform_and_org_rows(self):
        result = capture_snapshots(self.db)
        self.assertGreaterEqual(result["captured"], 2, "平台级 + 组织级各一行")
        rows = self.db.query(OutboxSnapshot).order_by(OutboxSnapshot.organization_id.asc()).all()
        by_org = {row.organization_id: row for row in rows}
        self.assertIn(None, by_org, "平台级快照 organization_id 为空")
        org_row = by_org[self.org_id]
        # 通知：pending 1 + approved 1 + sending 1 = 3；死信 1；卡死 1；待重试 1
        self.assertEqual(org_row.notification_backlog, 3)
        self.assertEqual(org_row.notification_dead_letter, 1)
        self.assertEqual(org_row.notification_stale_claims, 1)
        self.assertEqual(org_row.notification_retry_pending, 1)
        # 邮件：死信 1（sent 不算积压）
        self.assertEqual(org_row.email_backlog, 0)
        self.assertEqual(org_row.email_dead_letter, 1)

    def test_prune_respects_retention(self):
        now = utc_now()
        self.db.add(OutboxSnapshot(captured_at=now - timedelta(days=40), organization_id=None))
        self.db.add(OutboxSnapshot(captured_at=now - timedelta(days=2), organization_id=None))
        self.db.commit()
        with patch.object(get_settings(), "OUTBOX_SNAPSHOT_RETENTION_DAYS", 30):
            deleted = prune_snapshots(self.db, now=now)
        self.assertEqual(deleted, 1)
        remaining = self.db.query(OutboxSnapshot).all()
        self.assertEqual(len(remaining), 1)

    def test_history_platform_series_excludes_org_rows(self):
        now = utc_now()
        self.db.add_all([
            OutboxSnapshot(captured_at=now - timedelta(hours=2), organization_id=None,
                           notification_backlog=5, email_backlog=1),
            OutboxSnapshot(captured_at=now - timedelta(hours=1), organization_id=None,
                           notification_backlog=8, email_backlog=2),
            OutboxSnapshot(captured_at=now - timedelta(hours=1), organization_id=self.org_id,
                           notification_backlog=99, email_backlog=99),
        ])
        self.db.commit()
        data = history(self.db, hours=24, now=now)
        self.assertEqual(data["snapshot_count"], 2)
        self.assertEqual([point["notification"] for point in data["backlog"]], [5, 8])
        self.assertFalse(data["empty"])

    def test_history_org_series(self):
        now = utc_now()
        self.db.add_all([
            OutboxSnapshot(captured_at=now - timedelta(hours=1), organization_id=None,
                           notification_backlog=1),
            OutboxSnapshot(captured_at=now - timedelta(hours=1), organization_id=self.org_id,
                           notification_backlog=7),
        ])
        self.db.commit()
        data = history(self.db, hours=6, organization_id=self.org_id, now=now)
        self.assertEqual(data["snapshot_count"], 1)
        self.assertEqual(data["backlog"][0]["notification"], 7)

    def test_history_empty_without_snapshots(self):
        data = history(self.db, hours=24)
        self.assertTrue(data["empty"])
        self.assertEqual(data["backlog"], [])
        self.assertEqual(len(data["throughput"]), 24, "吞吐仍按小时补齐（即使没有快照）")

    def test_throughput_counts_created_and_delivered_per_hour(self):
        data = history(self.db, hours=3)
        totals = {
            "notification_created": sum(hour["notification_created"] for hour in data["throughput"]),
            "notification_delivered": sum(hour["notification_delivered"] for hour in data["throughput"]),
            "email_created": sum(hour["email_created"] for hour in data["throughput"]),
            "email_delivered": sum(hour["email_delivered"] for hour in data["throughput"]),
        }
        self.assertEqual(totals["notification_created"], 6)
        self.assertEqual(totals["notification_delivered"], 1)
        self.assertEqual(totals["email_created"], 2)
        self.assertEqual(totals["email_delivered"], 1)

    def test_history_hours_clamped(self):
        self.assertEqual(history(self.db, hours=0)["hours"], 1)
        self.assertEqual(history(self.db, hours=9999)["hours"], 168)


if __name__ == "__main__":
    unittest.main()
