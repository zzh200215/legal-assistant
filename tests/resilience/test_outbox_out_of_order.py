"""韧性测试：通知 outbox 乱序事件投递的最终一致性与幂等。

覆盖 app/services/notification/notification_service.py 的 dispatch/claim 语义与
app/tasks/notification_tasks.py::recover_stale_outbox_claims_task：
- 乱序投递：同一接收者的多个事件以任意顺序被 worker 领取，各自恰好投递一次
  （最终一致，无重发/漏发）；
- dispatch 幂等：重复调度不重复投递（claim 原子领取）；
- 断电恢复：sending 租约过期 → 回置 pending（recover 任务），重领不重复投递。
"""

import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal_notifications import LegalNotificationEvent
from app.models.org import Organization
from app.models.user import User
from app.services.notification.notification_service import (
    STATUS_DELIVERED,
    STATUS_PENDING,
    STATUS_SENDING,
    notification_service,
)
from app.tasks.notification_tasks import recover_stale_outbox_claims_task


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class NotificationOutboxOrderingTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="OutboxOrg", code="OBX")
        self.db.add(self.org)
        self.db.commit()
        self.user = User(username="obx", email="obx@example.com", hashed_password="h",
                         organization_id=self.org.id)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        self._patchers = [
            patch("app.tasks.notification_tasks.SessionLocal", self.Session),
            patch("app.tasks.notification_tasks._record_beat_heartbeat"),
            patch(
                "app.tasks.runtime.redis.from_url",
                side_effect=RuntimeError("redis unavailable in unit tests"),
            ),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.db.close()
        self.engine.dispose()

    def _event(self, event_type: str, *, reference_id: int, scheduled_at=None) -> LegalNotificationEvent:
        event = LegalNotificationEvent(
            organization_id=self.org.id,
            user_id=self.user.id,
            case_id=None,
            event_type=event_type,
            title=f"事件-{event_type}",
            body=f"ref:{reference_id}",
            channel="site",
            status=STATUS_PENDING,
            scheduled_at=scheduled_at or utc_now(),
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def test_out_of_order_dispatch_delivers_each_once(self):
        """乱序语义：同批事件以任意创建/领取顺序，最终各投递一次（最终一致）。"""
        # 两个事件均已到期，但创建顺序与投递顺序可以颠倒（乱序投递）
        first_created = self._event("deadline_reminder", reference_id=1,
                                    scheduled_at=utc_now() - timedelta(minutes=5))
        second_created = self._event("portal_notification", reference_id=2,
                                     scheduled_at=utc_now() - timedelta(minutes=1))
        # 乱序领取：先 claim 后创建的事件（模拟 worker 领取顺序颠倒）
        notification_service.transition(db=self.db, event=second_created, to=STATUS_SENDING)
        second_created.claimed_by = "worker-a"
        second_created.claim_expires_at = utc_now() + timedelta(minutes=5)
        self.db.commit()
        notification_service.dispatch_pending(db=self.db)  # 其余事件继续投递
        self.db.refresh(first_created)
        self.db.refresh(second_created)
        # 最终一致：两个事件各自恰好一次 delivered
        self.assertEqual(first_created.status, STATUS_DELIVERED)
        self.assertEqual(second_created.status, STATUS_SENDING)  # 已 claim，未重复投递
        # worker 完成投递后
        notification_service.transition(db=self.db, event=second_created, to=STATUS_DELIVERED)
        self.db.commit()  # autoflush=False：显式提交后再核对库内最终状态
        self.assertEqual(self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == STATUS_DELIVERED).count(), 2)

    def test_dispatch_is_idempotent_on_rerun(self):
        self._event("deadline_reminder", reference_id=3)
        notification_service.dispatch_pending(db=self.db)
        notification_service.dispatch_pending(db=self.db)  # 重复调度
        delivered = self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == STATUS_DELIVERED).count()
        self.assertEqual(delivered, 1)
        self.assertEqual(self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == STATUS_PENDING).count(), 0)

    def test_crash_recovery_reclaims_without_duplicate_delivery(self):
        """断电恢复：sending 租约过期 → recover 回置 pending → 重投递恰一次。"""
        event = self._event("deadline_reminder", reference_id=4)
        # worker 领取后崩溃（sending + 过期租约）
        notification_service.transition(db=self.db, event=event, to=STATUS_SENDING)
        event.claimed_by = "dead-worker"
        event.claim_expires_at = utc_now() - timedelta(minutes=30)
        self.db.commit()

        recover_stale_outbox_claims_task.run()
        self.db.refresh(event)
        self.assertEqual(event.status, STATUS_PENDING)
        self.assertIsNone(event.claimed_by)
        self.assertIsNone(event.claim_expires_at)

        # 重领并完成：恰好一次 delivered
        notification_service.dispatch_pending(db=self.db)
        self.db.refresh(event)
        self.assertEqual(event.status, STATUS_DELIVERED)
        self.assertEqual(self.db.query(LegalNotificationEvent).filter(
            LegalNotificationEvent.status == STATUS_DELIVERED).count(), 1)

    def test_live_claim_not_reclaimed(self):
        event = self._event("deadline_reminder", reference_id=5)
        notification_service.transition(db=self.db, event=event, to=STATUS_SENDING)
        event.claimed_by = "alive-worker"
        event.claim_expires_at = utc_now() + timedelta(minutes=30)
        self.db.commit()
        recover_stale_outbox_claims_task.run()
        self.db.refresh(event)
        self.assertEqual(event.status, STATUS_SENDING)  # 未过期不动


if __name__ == "__main__":
    unittest.main()
