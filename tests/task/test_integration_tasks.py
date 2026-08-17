"""Task 层：集成任务直调补测（账号注销/备份/飞书/连接器/邮箱同步）。

覆盖 app/tasks/integration_tasks.py 全部分支（含 feature 开关关闭跳过、
锁未获跳过、驱动不支持跳过、子进程失败、回收重投）。
"""

import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.connector import ExternalConnector
from app.models.org import Organization
from app.models.sync_run import SyncRun
from app.models.user import User
from app.tasks import integration_tasks as it


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class IntegrationTasksTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="IntOrg", code="ITG")
        self.db.add(self.org)
        self.db.commit()
        self.user = User(username="it", email="it@example.com", hashed_password="h",
                         role="user", organization_id=self.org.id)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self._patchers = [
            patch("app.tasks.integration_tasks.SessionLocal", self.Session),
            patch("app.tasks.integration_tasks._record_beat_heartbeat"),
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

    # ── 账号注销确认 ────────────────────────────────────────────────────────
    def test_confirm_account_deletions(self):
        with patch(
            "app.services.auth.account_deletion_service.confirm_expired_pending", return_value=3
        ):
            result = it.confirm_account_deletions_task.run()
        self.assertEqual(result, {"confirmed_count": 3})

    # ── 每日备份 ────────────────────────────────────────────────────────────
    def test_backup_skips_unsupported_driver(self):
        with patch.object(it, "get_settings") as settings:
            settings.return_value.DATABASE_URL = "sqlite:///data/app.db"
            result = it.create_pilot_backup_task.run()
        self.assertEqual(result["status"], "skipped")

    def test_backup_mysql_success(self):
        process = MagicMock(returncode=0, stdout='{"status": "ok", "backup_dir": "/bk/1"}', stderr="")
        with (
            patch.object(it, "get_settings") as settings,
            patch.object(it.subprocess, "run", return_value=process),
        ):
            settings.return_value.DATABASE_URL = "mysql+pymysql://u:p@h/db"
            settings.return_value.BACKUP_OUTPUT_DIR = "/out"
            settings.return_value.BACKUP_DATA_DIRS = ["/data1"]
            settings.return_value.BACKUP_OFFSITE_DIR = "/offsite"
            settings.return_value.BACKUP_RETENTION_COUNT = 7
            result = it.create_pilot_backup_task.run()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["backup_dir"], "/bk/1")

    def test_backup_subprocess_failure(self):
        with (
            patch.object(it, "get_settings") as settings,
            patch.object(it.subprocess, "run", side_effect=OSError("no disk")),
        ):
            settings.return_value.DATABASE_URL = "postgresql://u:p@h/db"
            result = it.create_pilot_backup_task.run()
        self.assertEqual(result["status"], "error")

    def test_backup_nonzero_exit(self):
        process = MagicMock(returncode=1, stdout="{}", stderr="backup failed")
        with (
            patch.object(it, "get_settings") as settings,
            patch.object(it.subprocess, "run", return_value=process),
        ):
            settings.return_value.DATABASE_URL = "mysql+pymysql://u:p@h/db"
            result = it.create_pilot_backup_task.run()
        self.assertEqual(result["status"], "error")

    # ── 飞书提醒 ────────────────────────────────────────────────────────────
    def test_dispatch_feishu_reminders(self):
        with patch(
            "app.services.integration.feishu_service.dispatch_feishu_reminders",
            AsyncMock(return_value={"bindings": 1, "sent_activation": 1, "sent_digest": 0}),
        ):
            result = it.dispatch_feishu_reminders_task.run()
        self.assertEqual(result["sent_activation"], 1)

    # ── 连接器同步 ──────────────────────────────────────────────────────────
    def test_connector_sync_disabled(self):
        with patch.object(it, "get_settings") as settings:
            settings.return_value.CONNECTOR_SYNC_ENABLED = False
            result = it.connector_sync_task.run(1)
        self.assertEqual(result, {"status": "disabled"})

    def test_connector_sync_lock_held(self):
        with (
            patch.object(it, "get_settings") as settings,
            patch.object(it, "_acquire_task_lock", return_value=None),
            patch("app.tasks.integration_tasks.log_async_task_event"),
        ):
            settings.return_value.CONNECTOR_SYNC_ENABLED = True
            result = it.connector_sync_task.run(1)
        self.assertEqual(result, {"status": "skipped", "reason": "lock_held"})

    def test_connector_sync_success(self):
        with (
            patch.object(it, "get_settings") as settings,
            patch.object(it, "_acquire_task_lock", return_value="tok"),
            patch.object(it, "_release_task_lock"),
            patch("app.services.integration.connector_sync_framework._run_connector_sync",
                  return_value={"status": "succeeded", "succeeded": 5}),
        ):
            settings.return_value.CONNECTOR_SYNC_ENABLED = True
            result = it.connector_sync_task.run(1)
        self.assertEqual(result["status"], "succeeded")

    def test_recover_stale_connector_syncs_disabled(self):
        with patch.object(it, "get_settings") as settings:
            settings.return_value.CONNECTOR_SYNC_ENABLED = False
            result = it.recover_stale_connector_syncs_task.run()
        self.assertEqual(result, {"recovered": 0})

    def test_recover_stale_connector_syncs_redispatches(self):
        conn = ExternalConnector(
            user_id=self.user.id, organization_id=self.org.id,
            connector_type="mock", name="c1", status="active",
        )
        self.db.add(conn)
        self.db.commit()
        self.db.refresh(conn)
        self.db.add(SyncRun(
            connector_id=conn.id, user_id=self.user.id, status="running",
            lease_expires_at=utc_now() - timedelta(hours=1),
        ))
        self.db.commit()
        with (
            patch.object(it, "get_settings") as settings,
            patch.object(it, "connector_sync_task") as sync_task,
        ):
            settings.return_value.CONNECTOR_SYNC_ENABLED = True
            settings.return_value.SYNC_RUN_LEASE_TTL_SECONDS = 600
            sync_task.delay.return_value = MagicMock(id="n1")
            result = it.recover_stale_connector_syncs_task.run()
        self.assertEqual(result, {"recovered": 1})
        sync_task.delay.assert_called_once()

    # ── 邮箱同步 ────────────────────────────────────────────────────────────
    def test_mailbox_sync_disabled(self):
        with patch.object(it, "get_settings") as settings:
            settings.return_value.MAILBOX_SYNC_ENABLED = False
            result = it.mailbox_sync_task.run(1)
        self.assertEqual(result, {"status": "disabled"})

    def test_mailbox_sync_account_not_found(self):
        with (
            patch.object(it, "get_settings") as settings,
            patch.object(it, "_acquire_task_lock", return_value="tok"),
            patch.object(it, "_release_task_lock"),
        ):
            settings.return_value.MAILBOX_SYNC_ENABLED = True
            result = it.mailbox_sync_task.run(9999)
        self.assertEqual(result, {"status": "error", "reason": "account_not_found"})

    def test_mailbox_sync_success(self):
        from app.models.mailbox import MailboxSyncAccount

        conn = ExternalConnector(
            user_id=self.user.id, organization_id=self.org.id,
            connector_type="mailbox", name="mail1", status="active",
        )
        self.db.add(conn)
        self.db.commit()
        self.db.refresh(conn)
        account = MailboxSyncAccount(connector_id=conn.id, user_id=self.user.id,
                                     email_address="m@example.com", status="active")
        self.db.add(account)
        self.db.commit()
        with (
            patch.object(it, "get_settings") as settings,
            patch.object(it, "_acquire_task_lock", return_value="tok"),
            patch.object(it, "_release_task_lock"),
            patch("app.services.integration.mailbox_sync_service.mailbox_sync_service") as svc,
        ):
            settings.return_value.MAILBOX_SYNC_ENABLED = True
            settings.return_value.SYNC_RUN_LEASE_TTL_SECONDS = 600
            svc.sync_account.return_value = {"status": "succeeded"}
            result = it.mailbox_sync_task.run(account.id)
        self.assertEqual(result["status"], "succeeded")

    def test_recover_stale_mailbox_syncs(self):
        with (
            patch.object(it, "get_settings") as settings,
            patch("app.services.integration.mailbox_sync_service.mailbox_sync_service") as svc,
            patch.object(it, "mailbox_sync_task") as sync_task,
        ):
            settings.return_value.MAILBOX_SYNC_ENABLED = True
            svc.recover_stale.return_value = [MagicMock(id=1), MagicMock(id=2)]
            sync_task.delay.return_value = MagicMock(id="n")
            result = it.recover_stale_mailbox_syncs_task.run()
        self.assertEqual(result, {"recovered": 2})

    def test_connector_context_resolution(self):
        conn = ExternalConnector(
            user_id=self.user.id, organization_id=self.org.id,
            connector_type="mock", name="c2", status="active",
        )
        self.db.add(conn)
        self.db.commit()
        self.db.refresh(conn)
        ctx = it._connector_context(self.db, conn.id)
        self.assertEqual(ctx, {"tenant_id": self.org.id, "user_id": self.user.id})
        self.assertEqual(it._connector_context(self.db, 9999), {})


if __name__ == "__main__":
    unittest.main()
