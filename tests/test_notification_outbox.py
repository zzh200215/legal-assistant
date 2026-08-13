"""通知 Outbox 化测试：创建/投递分离、状态机、claim 并发、租约回收、死信/人工重试、幂等。"""
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.config import get_settings
from app.core.database import Base
from app.core.time import utc_now
from app.models.email import EmailDraft, EmailSendRequest
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User, UserStatus
from app.services.notification_service import (
    NotificationStateError, notification_service,
)
from app.services.outbound_email_service import outbound_email_service


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=20):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, username, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class NotificationOutboxTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.Session = Session
        self.db = Session()

        org = Organization(name="OutboxOrg", code="OUTB")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id

        self.user = User(username="u1", email="u1@example.com",
                         hashed_password=hash_password("pw"), role="user",
                         status=UserStatus.active.value, organization_id=org.id)
        self.db.add(self.user)
        self.db.flush()
        self.user_id = self.user.id
        self.approver = User(username="admin", email="admin@example.com",
                             hashed_password=hash_password("pw"), role="admin",
                             status=UserStatus.active.value, organization_id=org.id)
        self.db.add(self.approver)
        self.db.commit()
        self.approver_id = self.approver.id

        outbound_email_service.update_policy(
            db=self.db, user=self.user,
            request=SimpleNamespace(enabled=True, allowed_recipient_domains=["example.com"],
                                    max_sends_per_hour=100, require_approval=True,
                                    dlp_enabled=True, dlp_action="block"),
        )
        self.connector = outbound_email_service.create_smtp_connector(
            db=self.db, user=self.user,
            request=SimpleNamespace(name="smtp", host="smtp.example.com", port=587,
                                    username="u1@example.com", password="pw",
                                    from_address="u1@example.com", use_starttls=True),
        )
        FakeSMTP.sent = []

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create_email_notification(self, **kw):
        params = dict(organization_id=self.org_id, user_id=self.user_id,
                      event_type="deadline", title="关键日期提醒", body="案件 X 有重要日期",
                      channel="email", reference_type="deadline", reference_id=10)
        params.update(kw)
        return notification_service.create_notification(db=self.db, **params)

    # ── 创建与投递分离：创建/分发不触发 SMTP，只有邮件 worker 才真正发送 ──────

    def test_email_send_happens_only_in_outbox_worker(self):
        event = self._create_email_notification()
        with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
            notification_service.dispatch_pending(db=self.db)
        self.assertEqual(len(FakeSMTP.sent), 0, "dispatch 只登记 Outbox，不真正发送")
        # 邮件 Outbox 请求已创建（自动批准）
        request = self.db.query(EmailSendRequest).filter(
            EmailSendRequest.notification_event_id == event.id).first()
        self.assertIsNotNone(request)
        self.assertEqual(request.status, "approved")
        self.db.refresh(event)
        self.assertEqual(event.status, "approved")
        # 邮件 worker 领取并真正发送
        with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
            owner = "worker-1"
            batch = outbound_email_service.claim_pending_batch(db=self.db, owner=owner)
            self.assertEqual(len(batch), 1)
            for req in batch:
                outbound_email_service._perform_send(db=self.db, request=req, owner=owner)
            self.db.commit()
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.db.refresh(request)
        self.db.refresh(event)
        self.assertEqual(request.status, "sent")
        self.assertEqual(event.status, "sent")  # 终态镜像回通知事件
        self.assertIsNotNone(event.provider_message_id)

    def test_business_rollback_produces_no_outbound(self):
        # 业务事务内创建通知 + Outbox 请求，回滚 → 都不持久化，不产生外发
        session = self.Session()
        try:
            user = session.query(User).filter(User.id == self.user_id).first()
            event = LegalNotificationEvent(
                organization_id=self.org_id, user_id=self.user_id,
                event_type="deadline", title="t", channel="email", status="pending")
            session.add(event)
            session.flush()
            request = outbound_email_service.create_notification_email(
                db=session, user=user, notification_event=event,
                subject="t", body="b", recipient="u1@example.com", auto_approve=True)
            self.assertIsNotNone(request)
            session.rollback()
        finally:
            session.close()
        self.assertEqual(self.db.query(LegalNotificationEvent).count(), 0)
        self.assertEqual(self.db.query(EmailSendRequest).count(), 0)
        self.assertEqual(self.db.query(EmailDraft).count(), 0)

    # ── 状态机 ──────────────────────────────────────────────────────

    def test_state_machine_rejects_illegal_transitions(self):
        event = self._create_email_notification()
        with self.assertRaises(NotificationStateError):
            notification_service.transition(db=self.db, event=event, to="sent")
        with self.assertRaises(NotificationStateError):
            notification_service.transition(db=self.db, event=event, to="read")
        # 合法迁移：pending -> sending
        notification_service.transition(db=self.db, event=event, to="sending")
        self.assertEqual(event.status, "sending")

    def test_no_sending_without_approval(self):
        # 关闭自动批准 → 需人工审批，worker 不得发送
        with patch.object(get_settings(), "AUTO_APPROVE_EMAIL_NOTIFICATION_TO_OWNER", False):
            event = self._create_email_notification()
            with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
                notification_service.dispatch_pending(db=self.db)
        self.db.refresh(event)
        request = self.db.query(EmailSendRequest).filter(
            EmailSendRequest.notification_event_id == event.id).first()
        self.assertIsNotNone(request)
        self.assertEqual(request.status, "pending")  # 等待审批
        self.assertEqual(event.status, "pending")
        with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
            outbound_email_service.claim_pending_batch(db=self.db, owner="w")
        self.assertEqual(len(FakeSMTP.sent), 0, "未审批不得发送")
        # 人工审批 → 通知镜像 approved → worker 发送
        outbound_email_service.decide_request(request.id, approved=True, note="ok",
                                              db=self.db, user=self.approver)
        self.db.refresh(event)
        self.assertEqual(event.status, "approved")
        with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
            owner = "w2"
            batch = outbound_email_service.claim_pending_batch(db=self.db, owner=owner)
            for req in batch:
                outbound_email_service._perform_send(db=self.db, request=req, owner=owner)
            self.db.commit()
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.db.refresh(event)
        self.assertEqual(event.status, "sent")

    def test_dlp_block_goes_to_dead_letter(self):
        event = self._create_email_notification(body="使用令牌 sk_abcdefghijklmnopqrstuvwxyz123456 访问")
        with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
            notification_service.dispatch_pending(db=self.db)
        self.db.refresh(event)
        self.assertEqual(event.status, "dead_letter")
        self.assertEqual(event.error_code, "DLP_BLOCKED")
        self.assertEqual(len(FakeSMTP.sent), 0)

    # ── 并发 claim：同一记录只被一个 worker 投递 ─────────────────────

    def test_concurrent_claim_is_disjoint(self):
        for i in range(6):
            self._create_email_notification(reference_id=100 + i)
        # 通知 dispatch claim
        a = notification_service._claim_events(self.db, "worker-a", utc_now(), 50)
        b = notification_service._claim_events(self.db, "worker-b", utc_now(), 50)
        a_ids = {e.id for e in a}
        b_ids = {e.id for e in b}
        self.assertTrue(a_ids.isdisjoint(b_ids), "并发 claim 不得重叠")
        self.assertEqual(len(a) + len(b), 6)

    def test_lease_expiry_recovery_no_duplicate(self):
        with patch.object(get_settings(), "NOTIFICATION_CLAIM_TTL_SECONDS", 1):
            event = self._create_email_notification()
            claimed = notification_service._claim_events(self.db, "dead-worker", utc_now(), 50)
            self.assertEqual(len(claimed), 1)
            self.assertEqual(event.status, "sending")
            # 租约过期 → 回收回退 pending
            event.claim_expires_at = utc_now() - timedelta(seconds=1)
            self.db.commit()
            from app.tasks import recover_stale_outbox_claims_task
            with patch("app.tasks.SessionLocal", self.Session):
                result = recover_stale_outbox_claims_task()
            self.db.refresh(event)
            self.assertEqual(event.status, "pending")
            self.assertGreaterEqual(result.get("notification_reclaimed", 0), 1)
            # 重新领取可正常投递
            with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
                notification_service.dispatch_pending(db=self.db)
            self.db.refresh(event)
            self.assertNotEqual(event.status, "sending")

    # ── 幂等创建 ────────────────────────────────────────────────────

    def test_duplicate_create_returns_existing(self):
        e1 = self._create_email_notification(reference_id=42)
        e2 = self._create_email_notification(reference_id=42)
        self.assertEqual(e1.id, e2.id, "同幂等键重复创建应返回已有记录")
        count = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.idempotency_key == e1.idempotency_key).count()
        self.assertEqual(count, 1)

    def test_email_notification_uses_template_rendering(self):
        from app.services.notification_template_service import notification_template_service

        notification_template_service.create_template(
            db=self.db, channel="email", template_key="deadline", locale="zh-CN",
            subject_template="到期提醒 {{title}}",
            body_template="案件提醒：{{title}}｜{{body}}",
            params_schema={"type": "object", "required": ["title", "body"]},
        )
        event = notification_service.create_notification(
            db=self.db, organization_id=self.org_id, user_id=self.user_id,
            event_type="deadline", title="关键日期提醒", body="案件 X 今日到期",
            channel="email", template_key="deadline", locale="zh-CN",
            reference_type="deadline", reference_id=88)
        with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
            notification_service.dispatch_pending(db=self.db)
        request = self.db.query(EmailSendRequest).filter(
            EmailSendRequest.notification_event_id == event.id).first()
        self.assertIsNotNone(request)
        draft = self.db.query(EmailDraft).filter(EmailDraft.id == request.draft_id).first()
        self.assertEqual(draft.subject, "到期提醒 关键日期提醒")
        self.assertIn("案件提醒：关键日期提醒｜案件 X 今日到期", draft.content)
        # 模板版本信息记录到通知事件
        self.db.refresh(event)
        self.assertEqual(event.template_key, "deadline")
        # worker 实际发送的即渲染后的主题
        with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
            owner = "w-tpl"
            batch = outbound_email_service.claim_pending_batch(db=self.db, owner=owner)
            for req in batch:
                outbound_email_service._perform_send(db=self.db, request=req, owner=owner)
            self.db.commit()
        self.assertEqual(FakeSMTP.sent[0]["Subject"], "到期提醒 关键日期提醒")

    def test_notification_manual_retry_cascades_to_email_dead_letter(self):
        # 通知关联的邮件请求已死信 → 通知级人工重试应一并重置请求待投递
        event = self._create_email_notification(reference_id=77)
        with patch("app.services.outbound_email_service.smtplib.SMTP", FakeSMTP):
            notification_service.dispatch_pending(db=self.db)
        self.db.refresh(event)
        request = self.db.query(EmailSendRequest).filter(
            EmailSendRequest.notification_event_id == event.id).first()
        self.assertIsNotNone(request)
        # 模拟邮件 worker 死信镜像：请求 + 通知事件都置死信
        request.status = "dead_letter"
        request.dead_letter_at = utc_now()
        request.dead_letter_reason = "test"
        request.attempt = 3
        notification_service.mark_dead_letter(db=self.db, event=event,
                                              reason="EMAIL_DEAD_LETTER", error_code="EMAIL_DEAD_LETTER")
        self.db.commit()
        self.assertEqual(event.status, "dead_letter")
        retried = notification_service.manual_retry(db=self.db, event_id=event.id, user=self.user)
        self.assertEqual(retried.status, "pending")
        self.db.refresh(request)
        self.assertEqual(request.status, "pending")
        self.assertEqual(request.attempt, 0)
        self.assertIsNone(request.dead_letter_at)

    def test_site_notification_delivers_via_claim(self):
        event = notification_service.create_notification(
            db=self.db, organization_id=self.org_id, user_id=self.user_id,
            event_type="deadline", title="站内提醒", channel="site",
            reference_type="deadline", reference_id=7)
        stats = notification_service.dispatch_pending(db=self.db)
        self.assertEqual(stats["delivered"], 1)
        self.db.refresh(event)
        self.assertEqual(event.status, "delivered")
        self.assertIsNotNone(event.sent_at)

    # ── 死信与人工重试 ──────────────────────────────────────────────

    def test_dead_letter_manual_retry_permission(self):
        # 无 SMTP 策略 → 死信
        outbound_email_service.update_policy(
            db=self.db, user=self.user,
            request=SimpleNamespace(enabled=False, allowed_recipient_domains=["example.com"],
                                    max_sends_per_hour=100, require_approval=True,
                                    dlp_enabled=True, dlp_action="block"),
        )
        event = self._create_email_notification(reference_id=5)
        notification_service.dispatch_pending(db=self.db)
        self.db.refresh(event)
        self.assertEqual(event.status, "dead_letter")
        self.assertEqual(event.error_code, "NO_SMTP_CONNECTOR")
        # 权限校验：非本人非 admin 不可重试
        outsider = User(username="outsider", email="o@example.com",
                        hashed_password=hash_password("pw"), role="user",
                        status=UserStatus.active.value, organization_id=self.org_id)
        self.db.add(outsider)
        self.db.commit()
        with self.assertRaises(ValueError):
            notification_service.manual_retry(db=self.db, event_id=event.id, user=outsider)
        # 本人可重试（保留原幂等键）
        retried = notification_service.manual_retry(db=self.db, event_id=event.id, user=self.user)
        self.assertEqual(retried.status, "pending")
        self.assertIsNotNone(retried.idempotency_key)


if __name__ == "__main__":
    unittest.main()
