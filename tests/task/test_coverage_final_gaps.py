"""Task 层：收官补测——文档/法律任务异常与授权分支（关键路径 80% 最后缺口）。

覆盖：
- document_tasks：parse_document 的权限快照分支、document_chunk/index 异常重试分支；
- legal_tasks：process_open_contract_review 处理异常 → failed + 重抛；
  parse_contract_versions 拆分异常 → failed；recover_queued 重投失败跳过分支。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.document import Document
from app.models.legal_contract import LegalContractVersion
from app.models.legal_platform import LegalAsyncJob, LegalAsyncJobInput
from app.models.org import Organization
from app.models.user import User
from app.tasks.legal_tasks import (
    parse_contract_versions_task,
    process_open_contract_review_task,
    recover_queued_open_contract_reviews_task,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _fake_task(task_id="task-f"):
    return SimpleNamespace(request=SimpleNamespace(id=task_id, retries=0), update_state=lambda **kw: None)


class FinalGapsTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="FinalGap", code="FNG")
        self.db.add(self.org)
        self.db.commit()
        self.user = User(username="fg", email="fg@example.com", hashed_password="h",
                         role="user", organization_id=self.org.id)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.patches = [
            patch("app.tasks.document_tasks.SessionLocal", self.Session),
            patch("app.tasks.legal_tasks.SessionLocal", self.Session),
            patch("app.tasks.document_tasks._acquire_document_lock", return_value=True),
            patch("app.tasks.document_tasks._release_document_lock"),
            patch("app.tasks.document_tasks.log_async_task_event"),
            patch("app.tasks.legal_tasks._record_beat_heartbeat"),
            patch(
                "app.tasks.runtime.redis.from_url",
                side_effect=RuntimeError("redis unavailable in unit tests"),
            ),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.db.close()
        self.engine.dispose()

    def _doc(self) -> Document:
        doc = Document(user_id=self.user.id, title="t.md", file_type="md",
                       status="uploaded", current_stage="uploaded", version_number=1,
                       organization_id=self.org.id)
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        return doc

    # ── document_tasks：权限快照 + 异常分支 ─────────────────────────────────
    def test_parse_document_checks_permission_snapshot(self):
        import app.tasks.document_tasks as dt

        doc = self._doc()
        with (
            patch("app.services.org.authorization_service.authorization_service.assert_snapshot") as check,
            patch.object(dt, "run_parse", return_value={"status": "skipped", "reason": "x"}),
        ):
            result = dt.parse_document_task.run.__func__(
                _fake_task(), document_id=doc.id, version_number=1, file_type="md", snapshot_id="snap-1")
        self.assertEqual(result["status"], "skipped")
        check.assert_called_once()

    def test_chunk_task_exception_triggers_retry(self):
        import app.tasks.document_tasks as dt

        doc = self._doc()
        with (
            patch.object(dt, "run_chunk", side_effect=RuntimeError("boom")),
            patch.object(dt, "_retry_task") as retry,
            self.assertRaises(RuntimeError),
        ):
            dt.document_chunk_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1)
        retry.assert_called_once()

    def test_index_task_exception_triggers_retry(self):
        import app.tasks.document_tasks as dt

        doc = self._doc()
        with (
            patch.object(dt, "run_index", side_effect=RuntimeError("boom")),
            patch.object(dt, "_retry_task") as retry,
            self.assertRaises(RuntimeError),
        ):
            dt.document_index_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1)
        retry.assert_called_once()

    # ── legal_tasks：异常分支 + 重投失败跳过 ────────────────────────────────
    def test_process_review_internal_error_fails(self):
        """处理中途异常 → 任务置 failed（可重试）并重抛。"""
        job = LegalAsyncJob(organization_id=self.org.id, job_type="open_contract_review",
                            status="queued", created_by=self.user.id)
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        self.db.add(LegalAsyncJobInput(
            job_id=job.id, app_id=1, request_fingerprint="f" * 64,
            title="合同", content_ciphertext="合同正文",
        ))
        self.db.commit()
        with (
            patch("app.tasks.legal_tasks.json.dumps", side_effect=RuntimeError("boom")),
            self.assertRaises(RuntimeError),
        ):
            process_open_contract_review_task.run(job.id)
        self.db.refresh(job)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.retry_count, 1)
        self.assertIsNotNone(job.error_summary)

    def test_parse_contract_versions_split_exception_fails(self):
        ver = LegalContractVersion(
            organization_id=self.org.id, contract_id=1, version_no=1,
            parse_status="uploading", text_snapshot="第一条 内容",
            created_by=self.user.id,
        )
        self.db.add(ver)
        self.db.commit()
        self.db.refresh(ver)
        with patch("re.split", side_effect=RuntimeError("split boom")):
            parse_contract_versions_task.run()  # except 分支：ver 置 failed，不抛
        self.db.refresh(ver)
        self.assertEqual(ver.parse_status, "failed")

    def test_recover_queued_redispatch_failure_continues(self):
        for _ in range(2):
            job = LegalAsyncJob(organization_id=self.org.id, job_type="open_contract_review",
                                status="queued", created_by=self.user.id)
            self.db.add(job)
        self.db.commit()
        with patch("app.tasks.legal_tasks.process_open_contract_review_task") as process:
            process.delay.side_effect = [RuntimeError("broker down"), MagicMock(id="ok")]
            result = recover_queued_open_contract_reviews_task.run()
        self.assertEqual(result["dispatched"], 1)  # 第一条失败跳过，第二条成功
        self.assertEqual(result["expired"], 0)


if __name__ == "__main__":
    unittest.main()
