"""文档处理流水线：幂等（parse/chunk/index 重复执行不产生重复产物）、版本守卫、
租约（lease）与回收、内容去重、异步入队。"""

import hashlib
import io
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.document import Document, DocumentChunk, DocumentParseArtifact, DocumentParseJob
from app.models.user import User
from app.services.documents.document_job_service import document_job_service
from app.services.documents.document_pipeline import run_chunk, run_index, run_parse
from app.services.documents.document_service import document_service
from app.services.storage.storage_service import LocalStorageAdapter, storage_service


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()
        self.user = User(username="pipe", email="pipe@example.com", hashed_password="h")
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.tmpdir = tempfile.mkdtemp()
        self._orig_adapter = storage_service._adapter
        storage_service._adapter = LocalStorageAdapter(self.tmpdir)

    def tearDown(self):
        storage_service._adapter = self._orig_adapter
        self.db.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_doc(self, content: str = "# 合同\n\n付款条款：甲方应在 2026-07-01 前支付首付款。", title: str = "合同.md") -> Document:
        raw = content.encode("utf-8")
        doc = Document(
            user_id=self.user.id,
            title=title,
            file_type="md",
            content_hash=_sha(raw),
            status="uploaded",
            current_stage="uploaded",
            version_number=1,
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        doc.object_key = storage_service.build_object_key(
            user_id=self.user.id, document_id=doc.id, version_number=doc.version_number, file_ext="md"
        )
        with io.BytesIO(raw) as stream:
            storage_service.put_stream(doc.object_key, stream, content_type="text/markdown")
        self.db.add(doc)
        self.db.commit()
        return doc

    def _index_recorder(self):
        calls = []

        def recorder(document_id, chunks, *, user_id=None, knowledge_base_id=None):
            calls.append({"document_id": document_id, "chunks": chunks, "user_id": user_id})
            return None

        return recorder, calls

    def _run_full(self, doc):
        parse = run_parse(self.db, doc.id, user_id=self.user.id)
        self.assertEqual(parse["status"], "success")
        chunk = run_chunk(self.db, doc.id)
        self.assertEqual(chunk["status"], "success")
        return parse, chunk


class PipelineIdempotencyTests(PipelineTestCase):
    def test_full_local_flow_parse_chunk_index(self):
        doc = self._make_doc()
        recorder, calls = self._index_recorder()
        parse = run_parse(self.db, doc.id, user_id=self.user.id)
        self.assertEqual(parse["status"], "success")
        chunk = run_chunk(self.db, doc.id)
        self.assertEqual(chunk["status"], "success")
        index = run_index(self.db, doc.id, user_id=self.user.id, index_chunks=recorder)
        self.assertEqual(index["status"], "success")

        self.db.refresh(doc)
        self.assertEqual(doc.status, "indexed")
        self.assertEqual(doc.current_stage, "indexed")
        chunks = self.db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).all()
        self.assertGreaterEqual(len(chunks), 1)
        artifact = (
            self.db.query(DocumentParseArtifact)
            .filter(DocumentParseArtifact.document_id == doc.id)
            .first()
        )
        self.assertIsNotNone(artifact)
        self.assertIsNotNone(artifact.artifact_hash)
        self.assertIsNotNone(artifact.parser_version)
        self.assertEqual(len(calls), 1)

    def test_parse_replay_is_idempotent(self):
        doc = self._make_doc()
        first = run_parse(self.db, doc.id, user_id=self.user.id)
        second = run_parse(self.db, doc.id, user_id=self.user.id)
        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "replayed")
        artifacts = self.db.query(DocumentParseArtifact).filter(DocumentParseArtifact.document_id == doc.id).all()
        self.assertEqual(len(artifacts), 1)

    def test_chunk_rerun_does_not_duplicate_chunks(self):
        doc = self._make_doc()
        run_parse(self.db, doc.id, user_id=self.user.id)
        run_chunk(self.db, doc.id)
        count_before = self.db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).count()
        rerun = run_chunk(self.db, doc.id)
        self.assertEqual(rerun["status"], "replayed")
        count_after = self.db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).count()
        self.assertEqual(count_after, count_before)

    def test_index_skipped_when_already_indexed(self):
        doc = self._make_doc()
        recorder, calls = self._index_recorder()
        run_parse(self.db, doc.id, user_id=self.user.id)
        run_chunk(self.db, doc.id)
        first = run_index(self.db, doc.id, user_id=self.user.id, index_chunks=recorder)
        second = run_index(self.db, doc.id, user_id=self.user.id, index_chunks=recorder)
        self.assertEqual(first["status"], "success")
        # 幂等键命中：replayed（跨重启重放）或 skipped（指纹一致已索引），均不重复写入
        self.assertIn(second["status"], ("replayed", "skipped"))
        self.assertEqual(len(calls), 1)

    def test_version_guard_skips_stale_task(self):
        doc = self._make_doc()
        result = run_parse(self.db, doc.id, expected_version=99, user_id=self.user.id)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "version_changed")

    def test_old_version_task_does_not_touch_new_version(self):
        doc_v1 = self._make_doc("# 版本一\n\n版本一正文内容", title="合同.md")
        doc_v2 = self._make_doc("# 版本二\n\n版本二新增正文内容", title="合同.md")
        doc_v2.version_number = 2
        self.db.add(doc_v2)
        self.db.commit()
        self.db.refresh(doc_v2)

        recorder, _ = self._index_recorder()
        for doc in (doc_v1, doc_v2):
            run_parse(self.db, doc.id, user_id=self.user.id)
            run_chunk(self.db, doc.id)
            run_index(self.db, doc.id, user_id=self.user.id, index_chunks=recorder)

        v1_chunks = self.db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_v1.id).all()
        v2_chunks = self.db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_v2.id).all()
        self.assertTrue(all(c.document_id == doc_v1.id for c in v1_chunks))
        self.assertTrue(all(c.document_id == doc_v2.id for c in v2_chunks))
        self.assertGreaterEqual(len(v1_chunks), 1)
        self.assertGreaterEqual(len(v2_chunks), 1)

        # 重跑 v1 的 chunk：不得触碰 v2 的切片
        run_chunk(self.db, doc_v1.id)
        v2_after = self.db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_v2.id).all()
        self.assertEqual([c.chunk_index for c in v2_after], [c.chunk_index for c in v2_chunks])

    def test_index_degraded_when_indexing_fails(self):
        doc = self._make_doc()
        run_parse(self.db, doc.id, user_id=self.user.id)
        run_chunk(self.db, doc.id)

        def failing(document_id, chunks, *, user_id=None, knowledge_base_id=None):
            return RuntimeError("embedding service unavailable")

        result = run_index(self.db, doc.id, user_id=self.user.id, index_chunks=failing)
        self.assertEqual(result["status"], "degraded")
        self.db.refresh(doc)
        self.assertEqual(doc.status, "parsed")
        self.assertEqual(doc.failure_stage, "indexing")


class UploadDedupAndAsyncTests(PipelineTestCase):
    def test_duplicate_upload_returns_existing_document(self):
        content = "# 合同\n\n重复上传内容".encode("utf-8")
        with patch("app.services.documents.document_service.rag_service.index_document", return_value=None):
            first = document_service.upload(
                self._stub_file("合同.md", content), user_id=self.user.id, db=self.db, async_mode=False
            )
            second = document_service.upload(
                self._stub_file("合同.md", content), user_id=self.user.id, db=self.db, async_mode=False
            )
        self.assertEqual(first.id, second.id)
        docs = self.db.query(Document).filter(Document.user_id == self.user.id).all()
        self.assertEqual(len(docs), 1)
        chunks = self.db.query(DocumentChunk).filter(DocumentChunk.document_id == first.id).all()
        self.assertGreaterEqual(len(chunks), 1)

    def test_async_upload_enqueues_parse_task(self):
        content = "# 标题\n正文内容".encode("utf-8")
        fake_task = SimpleNamespace(id="task-abc")
        with patch("app.tasks.parse_document_task.delay", return_value=fake_task) as delay_mock:
            doc = document_service.upload(
                self._stub_file("a.md", content), user_id=self.user.id, db=self.db, async_mode=True
            )
        delay_mock.assert_called_once()
        args = delay_mock.call_args.args
        self.assertEqual(args[0], doc.id)
        self.assertEqual(args[1], doc.version_number)
        self.assertEqual(args[2], "md")
        jobs = self.db.query(DocumentParseJob).filter(DocumentParseJob.document_id == doc.id).all()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].task_id, "task-abc")

    def _stub_file(self, filename, content):
        return SimpleNamespace(filename=filename, file=io.BytesIO(content))


class LeaseAndRecoveryTests(PipelineTestCase):
    def test_claim_renew_release_lease(self):
        job = document_job_service.create_job(
            document_id=1, user_id=self.user.id, job_type="document_parse", db=self.db
        )
        self.assertTrue(document_job_service.claim_job(job.id, "owner-1", ttl_seconds=300, db=self.db))
        self.assertFalse(document_job_service.claim_job(job.id, "owner-2", ttl_seconds=300, db=self.db))
        self.assertTrue(document_job_service.renew_lease(job.id, "owner-1", ttl_seconds=300, db=self.db))
        self.assertFalse(document_job_service.renew_lease(job.id, "owner-2", ttl_seconds=300, db=self.db))
        document_job_service.release_lease(job.id, "owner-1", db=self.db)
        refreshed = self.db.get(DocumentParseJob, job.id)
        self.assertIsNone(refreshed.lease_owner)
        self.assertIsNone(refreshed.lease_expires_at)

    def test_expired_lease_can_be_reclaimed(self):
        job = document_job_service.create_job(
            document_id=1, user_id=self.user.id, job_type="document_parse", db=self.db, status="running"
        )
        job.lease_owner = "stale-worker"
        job.lease_expires_at = utc_now() - timedelta(seconds=1)
        self.db.add(job)
        self.db.commit()
        self.assertTrue(document_job_service.claim_job(job.id, "new-worker", ttl_seconds=300, db=self.db))

    def test_recovery_plans_and_resets_stale_jobs(self):
        doc = self._make_doc()
        job = document_job_service.create_job(
            document_id=doc.id, user_id=self.user.id, job_type="document_chunk", db=self.db, status="running"
        )
        job.lease_owner = "dead-worker"
        job.lease_expires_at = utc_now() - timedelta(seconds=10)
        self.db.add(job)
        self.db.commit()

        stale_before = utc_now() + timedelta(seconds=5)
        plans = document_job_service.plan_recovery(self.db, stale_before=stale_before, limit=50)
        self.assertEqual(len(plans), 1)
        planned_job, task_name = plans[0]
        self.assertEqual(planned_job.id, job.id)
        self.assertEqual(task_name, "chunk")
        refreshed = self.db.get(DocumentParseJob, job.id)
        self.assertEqual(refreshed.status, "pending")
        self.assertIsNone(refreshed.lease_owner)

    def test_recovery_dispatch_by_job_type(self):
        self.assertEqual(document_job_service._recovery_task_for_job_type("document_parse"), "parse")
        self.assertEqual(document_job_service._recovery_task_for_job_type("document_chunk"), "chunk")
        self.assertEqual(document_job_service._recovery_task_for_job_type("document_index"), "index")
        self.assertEqual(document_job_service._recovery_task_for_job_type(None), "parse")


if __name__ == "__main__":
    unittest.main()
