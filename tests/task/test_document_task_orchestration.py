"""Task 层：文档任务编排器直调测试（parse/chunk/index/summarize/analyze/recover/export）。

覆盖 app/tasks/document_tasks.py：
- 流水线编排：parse → chunk → index 各阶段状态分支（skipped/degraded/permanent error/重试）；
- 独立切分/索引任务的状态处理与链式推进；
- recover_stale_document_jobs_task 的按类型重投与 job 绑定；
- 摘要/分析任务的进度上报、结果落库与重试。

调用方式：bind=True 任务用 `task.run.__func__(fake_self, ...)` 直调函数本体；
非 bind 任务用 `task.run(...)`。编排依赖（锁/日志/流水线阶段/重试）mock，
服务层（document_job_service 等）走 SQLite 内存库真实执行。
"""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.document import Document, DocumentParseJob
from app.models.user import User
from app.services.documents.document_parsing import DocumentParsePermanentError
from app.tasks import document_tasks as dt


def _fake_task(task_id: str = "task-t1") -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(id=task_id, retries=0),
        update_state=lambda **kw: None,
    )


class DocumentTaskOrchestrationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.Session()
        self.user = User(username="doc", email="doc@example.com", hashed_password="h")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        self._patchers = [
            patch("app.tasks.document_tasks.SessionLocal", self.Session),
            patch("app.tasks.document_tasks._acquire_document_lock", return_value=True),
            patch("app.tasks.document_tasks._release_document_lock"),
            patch("app.tasks.document_tasks.log_async_task_event"),
            # beat 锁（recover 任务）确定性阻断 redis：fail-open 放行
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

    def _add_doc(self, *, status="uploaded", stage="uploaded") -> Document:
        doc = Document(
            user_id=self.user.id,
            title="合同.md",
            file_type="md",
            status=status,
            current_stage=stage,
            version_number=1,
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        return doc

    def _add_job(self, doc_id: int, job_type: str, task_id: str, *, stale: bool = False) -> DocumentParseJob:
        job = DocumentParseJob(
            document_id=doc_id,
            user_id=self.user.id,
            job_type=job_type,
            task_id=task_id,
            status="running",  # list_stale_jobs 只认 running/pending
            lease_owner="dead-worker" if stale else "alive",
            lease_expires_at=utc_now() - timedelta(hours=1) if stale else utc_now() + timedelta(hours=1),
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    # ── parse_document_task ─────────────────────────────────────────────────
    def test_parse_full_pipeline_success(self):
        doc = self._add_doc()
        with (
            patch.object(dt, "run_parse", return_value={"status": "success", "chunks": 3}),
            patch.object(dt, "run_chunk", return_value={"status": "success", "chunks": 3}),
            patch.object(dt, "run_index", return_value={"status": "success"}),
        ):
            result = dt.parse_document_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1, file_type="md")
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["indexed"])
        self.assertEqual(result["chunks"], 3)

    def test_parse_document_not_found(self):
        result = dt.parse_document_task.run.__func__(_fake_task(), document_id=9999, version_number=1, file_type="md")
        self.assertEqual(result, {"status": "error", "message": "Document not found"})

    def test_parse_skipped_stops_pipeline(self):
        doc = self._add_doc()
        with (
            patch.object(dt, "run_parse", return_value={"status": "skipped", "reason": "version_mismatch"}),
            patch.object(dt, "run_chunk") as chunk,
            patch.object(dt, "run_index") as index,
        ):
            result = dt.parse_document_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1, file_type="md")
        self.assertEqual(result, {"status": "skipped", "reason": "version_mismatch", "document_id": doc.id})
        chunk.assert_not_called()
        index.assert_not_called()

    def test_parse_chunk_skipped_stops_index(self):
        doc = self._add_doc()
        with (
            patch.object(dt, "run_parse", return_value={"status": "success", "chunks": 2}),
            patch.object(dt, "run_chunk", return_value={"status": "skipped", "reason": "no_change"}),
            patch.object(dt, "run_index") as index,
        ):
            result = dt.parse_document_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1, file_type="md")
        self.assertEqual(result["status"], "skipped")
        index.assert_not_called()

    def test_parse_index_degraded_marks_succeeded_with_degradation(self):
        doc = self._add_doc()
        with (
            patch.object(dt, "run_parse", return_value={"status": "success", "chunks": 2}),
            patch.object(dt, "run_chunk", return_value={"status": "success", "chunks": 2}),
            patch.object(dt, "run_index", return_value={"status": "degraded"}),
        ):
            result = dt.parse_document_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1, file_type="md")
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["indexed"])
        job = self.db.query(DocumentParseJob).filter(DocumentParseJob.job_type == "document_parse").first()
        self.assertEqual(job.status, "succeeded")
        self.assertIn("降级", job.message)

    def test_parse_permanent_error_marks_failed_no_retry(self):
        doc = self._add_doc()
        with (
            patch.object(dt, "run_parse", side_effect=DocumentParsePermanentError("无法解析")),
            patch.object(dt, "_retry_task") as retry,
        ):
            result = dt.parse_document_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1, file_type="md")
        self.assertEqual(result["status"], "error")
        retry.assert_not_called()
        job = self.db.query(DocumentParseJob).filter(DocumentParseJob.job_type == "document_parse").first()
        self.assertEqual(job.status, "failed")

    def test_parse_generic_error_triggers_retry_and_reraises(self):
        doc = self._add_doc()
        with (
            patch.object(dt, "run_parse", side_effect=RuntimeError("boom")),
            patch.object(dt, "_retry_task") as retry,
            self.assertRaises(RuntimeError),
        ):
            dt.parse_document_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1, file_type="md")
        retry.assert_called_once()
        self.assertEqual(retry.call_args.kwargs["action_prefix"], "document_parse")

    # ── document_chunk_task / document_index_task ────────────────────────────
    def test_chunk_task_success_chains_index(self):
        doc = self._add_doc()
        with (
            patch.object(dt, "run_chunk", return_value={"status": "success", "chunks": 2}),
            patch.object(dt, "document_index_task") as index_task,
        ):
            index_task.delay.return_value = MagicMock(id="idx-1")
            result = dt.document_chunk_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1)
        self.assertEqual(result["status"], "success")
        index_task.delay.assert_called_once()
        job = self.db.query(DocumentParseJob).filter(DocumentParseJob.job_type == "document_chunk").first()
        self.assertEqual(job.status, "succeeded")

    def test_index_task_success(self):
        doc = self._add_doc()
        with patch.object(dt, "run_index", return_value={"status": "success", "indexed": 2}):
            result = dt.document_index_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1)
        self.assertEqual(result["status"], "success")
        job = self.db.query(DocumentParseJob).filter(DocumentParseJob.job_type == "document_index").first()
        self.assertEqual(job.status, "succeeded")

    def test_index_task_degraded(self):
        doc = self._add_doc()
        with patch.object(dt, "run_index", return_value={"status": "degraded"}):
            result = dt.document_index_task.run.__func__(_fake_task(), document_id=doc.id, version_number=1)
        self.assertEqual(result["status"], "degraded")
        job = self.db.query(DocumentParseJob).filter(DocumentParseJob.job_type == "document_index").first()
        self.assertIn("降级", job.message)

    # ── document_export_task（显式未实现，fail loudly）────────────────────────
    def test_export_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            dt.document_export_task.run(document_id=1, export_type="archive", user_id=1)

    # ── recover_stale_document_jobs_task ─────────────────────────────────────
    def test_recover_dispatches_by_job_type_and_rebinds_task_id(self):
        doc = self._add_doc()
        self._add_job(doc.id, "document_chunk", "old-chunk", stale=True)
        self._add_job(doc.id, "document_index", "old-index", stale=True)
        self._add_job(doc.id, "document_parse", "old-parse", stale=True)
        with (
            patch.object(dt, "document_chunk_task") as chunk_task,
            patch.object(dt, "document_index_task") as index_task,
            patch.object(dt, "parse_document_task") as parse_task,
        ):
            chunk_task.delay.return_value = MagicMock(id="new-chunk")
            index_task.delay.return_value = MagicMock(id="new-index")
            parse_task.delay.return_value = MagicMock(id="new-parse")
            result = dt.recover_stale_document_jobs_task.run()
        self.assertEqual(result, {"recovered": 3})
        chunk_task.delay.assert_called_once()
        index_task.delay.assert_called_once()
        parse_task.delay.assert_called_once()
        # 每个 job 复用原记录并绑定新 task_id
        self.assertEqual(self.db.query(DocumentParseJob).filter(DocumentParseJob.job_type == "document_chunk").first().task_id, "new-chunk")
        self.assertEqual(self.db.query(DocumentParseJob).filter(DocumentParseJob.job_type == "document_index").first().task_id, "new-index")
        self.assertEqual(self.db.query(DocumentParseJob).filter(DocumentParseJob.job_type == "document_parse").first().task_id, "new-parse")

    def test_recover_no_stale_jobs(self):
        self._add_doc()
        result = dt.recover_stale_document_jobs_task.run()
        self.assertEqual(result, {"recovered": 0})

    # ── summarize_document_task / analyze_document_task ──────────────────────
    def test_summarize_success_saves_summary(self):
        doc = self._add_doc()
        self._add_job(doc.id, "document_summarize", "task-t1")
        with (
            patch("app.services.documents.document_service.document_service.summarize", return_value="原文"),
            patch(
                "app.services.documents.analysis_service.analysis_service.summarize_document",
                AsyncMock(return_value="生成的摘要"),
            ),
        ):
            result = dt.summarize_document_task.run.__func__(_fake_task(), document_id=doc.id, user_id=self.user.id)
        self.assertEqual(result["summary"], "生成的摘要")
        self.db.refresh(doc)
        self.assertEqual(doc.summary, "生成的摘要")
        job = self.db.query(DocumentParseJob).filter(DocumentParseJob.task_id == "task-t1").first()
        self.assertEqual(job.status, "succeeded")

    def test_summarize_error_retries_without_reraising(self):
        doc = self._add_doc()
        with (
            patch("app.services.documents.document_service.document_service.summarize", side_effect=RuntimeError("boom")),
            patch.object(dt, "_retry_task") as retry,
        ):
            result = dt.summarize_document_task.run.__func__(_fake_task(), document_id=doc.id, user_id=self.user.id)
        self.assertIsNone(result)
        retry.assert_called_once()
        self.assertEqual(retry.call_args.kwargs["action_prefix"], "document_summary")

    def test_analyze_success(self):
        doc = self._add_doc()
        with patch(
            "app.services.documents.document_service.document_service.analyze",
            AsyncMock(return_value={"analysis_status": "success", "summary": "分析结果"}),
        ):
            result = dt.analyze_document_task.run.__func__(_fake_task(), document_id=doc.id, user_id=self.user.id)
        self.assertEqual(result["analysis_status"], "success")
        job = self.db.query(DocumentParseJob).filter(DocumentParseJob.task_id == "task-t1").first()
        self.assertIsNone(job)  # 无 task_id 记录时 mark_* 为 no-op，任务仍成功返回

    def test_analyze_partial_marks_degraded_message(self):
        doc = self._add_doc()
        self._add_job(doc.id, "document_analyze", "task-t1")
        with patch(
            "app.services.documents.document_service.document_service.analyze",
            AsyncMock(return_value={"analysis_status": "partial", "summary": "部分结果"}),
        ):
            result = dt.analyze_document_task.run.__func__(_fake_task(), document_id=doc.id, user_id=self.user.id)
        self.assertEqual(result["analysis_status"], "partial")
        job = self.db.query(DocumentParseJob).filter(DocumentParseJob.task_id == "task-t1").first()
        self.assertIn("部分降级", job.message)


if __name__ == "__main__":
    unittest.main()
