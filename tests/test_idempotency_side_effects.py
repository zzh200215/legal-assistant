"""幂等与防重复副作用测试：邮件确定性键、通知单投递、账单单状态迁移。

验收映射：双投递单事件 / 单状态迁移 / 单 SMTP 发送（SMTP 单发送已由
test_outbound_email_service.py 覆盖，这里聚焦新引入的确定性幂等键行为）。
"""
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.email import EmailDraft
from app.models.legal_billing import LegalInvoice
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User
from app.services.notification_service import notification_service
from app.services.outbound_email_service import outbound_email_service


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class EmailDeterministicIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(username="sender", email="sender@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.draft = EmailDraft(
            user_id=self.user.id, subject="项目同步", recipient="team@example.com",
            content="请确认本周进度。", status="draft", generation_type="generate",
            tone="professional",
        )
        self.db.add(self.draft)
        self.db.commit()
        self.db.refresh(self.draft)
        outbound_email_service.update_policy(
            db=self.db, user=self.user,
            request=SimpleNamespace(enabled=True, allowed_recipient_domains=["example.com"],
                                    max_sends_per_hour=2, require_approval=True,
                                    dlp_enabled=False, dlp_action="block"),
        )
        self.connector = outbound_email_service.create_smtp_connector(
            db=self.db, user=self.user,
            request=SimpleNamespace(name="SMTP", host="smtp.example.com", port=587,
                                    username="sender@example.com", password="app-password",
                                    from_address="sender@example.com", use_starttls=True),
        )

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_request_send_same_draft_returns_same_row(self):
        """确定性幂等键：同草稿 + 同内容重复请求 → 返回同一行，不重复建单。"""
        first = outbound_email_service.request_send(
            self.draft.id, connector_id=self.connector.id, db=self.db, user=self.user)
        second = outbound_email_service.request_send(
            self.draft.id, connector_id=self.connector.id, db=self.db, user=self.user)
        self.assertEqual(first.id, second.id)
        self.assertIn("email:", second.idempotency_key)
        from app.models.email import EmailSendRequest
        count = self.db.query(EmailSendRequest).filter(EmailSendRequest.draft_id == self.draft.id).count()
        self.assertEqual(count, 1, "重复请求不得产生第二张发送单")

    def test_failed_request_reused_and_reset_to_pending(self):
        """failed 的确定性键请求被复用并重置为 pending（重试不重复建单）。"""
        first = outbound_email_service.request_send(
            self.draft.id, connector_id=self.connector.id, db=self.db, user=self.user)
        from app.models.email import EmailSendRequest
        self.db.query(EmailSendRequest).filter(EmailSendRequest.id == first.id).update(
            {"status": "failed", "error_message": "boom"}, synchronize_session=False)
        self.db.commit()
        second = outbound_email_service.request_send(
            self.draft.id, connector_id=self.connector.id, db=self.db, user=self.user)
        self.assertEqual(first.id, second.id, "失败后复用同一确定性键行")
        self.assertEqual(second.status, "pending")
        self.assertIsNone(second.error_message)
        count = self.db.query(EmailSendRequest).filter(EmailSendRequest.draft_id == self.draft.id).count()
        self.assertEqual(count, 1)


class NotificationSingleDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        org = Organization(name="演示律所", code="demo-org")
        self.db.add(org)
        self.db.flush()
        self.user = User(username="n", email="n@example.com", hashed_password="secret",
                         organization_id=org.id)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.org_id = org.id

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_double_dispatch_delivers_once(self):
        """双投递：pending 事件投递一次后置 delivered，第二次 dispatch 不再命中。"""
        ev = LegalNotificationEvent(
            organization_id=self.org_id, user_id=self.user.id, event_type="deadline_reminder",
            title="关键日期提醒", channel="site", status="pending",
        )
        self.db.add(ev)
        self.db.commit()
        stats = notification_service.dispatch_pending(db=self.db)
        self.assertEqual(stats["delivered"], 1)
        self.db.refresh(ev)
        self.assertEqual(ev.status, "delivered")
        second = notification_service.dispatch_pending(db=self.db)
        self.assertEqual(second["delivered"], 0, "已投递事件不得重复投递")


class BillingSingleTransitionTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(username="b", email="b@example.com", hashed_password="secret")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_scan_overdue_single_state_transition(self):
        """账单扫描状态机天然幂等：sent→overdue 后不再命中，二次扫描 0 变更。"""
        invoice = LegalInvoice(
            organization_id=1, case_id=1, created_by=self.user.id,
            invoice_no="INV-001", client_display_name="演示客户",
            issue_date=date.today() - timedelta(days=30),
            total_amount="100.00", status="sent",
            due_date=date.today() - timedelta(days=3),
        )
        self.db.add(invoice)
        self.db.commit()
        with patch("app.tasks.SessionLocal", return_value=self.db):
            from app.tasks import scan_overdue_invoices_task
            first = scan_overdue_invoices_task()
            self.assertEqual(first["marked_overdue"], 1)
            second = scan_overdue_invoices_task()
            self.assertEqual(second["marked_overdue"], 0, "已 overdue 不再重复流转")
        # 任务 finally 关闭了传入的 session → 用新 session 复查状态
        self.db.close()
        fresh = sessionmaker(bind=self.engine)()
        try:
            inv = fresh.query(LegalInvoice).filter_by(invoice_no="INV-001").first()
            self.assertEqual(inv.status, "overdue")
        finally:
            fresh.close()


if __name__ == "__main__":
    unittest.main()
