"""可重建性：产物按版本记录版本号；parser/chunker/embedding 版本变化触发对应阶段重建。"""

import hashlib
import io
import shutil
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base
from app.models.document import Document, DocumentParseArtifact
from app.models.user import User
from app.services import document_pipeline
from app.services.document_pipeline import run_chunk, run_index, run_parse
from app.services.storage_service import LocalStorageAdapter, storage_service


class RebuildTestCase(unittest.TestCase):
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
        self.user = User(username="rebuild", email="rebuild@example.com", hashed_password="h")
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

    def _make_doc(self) -> Document:
        content = "# 合同\n\n付款条款内容".encode("utf-8")
        doc = Document(
            user_id=self.user.id,
            title="合同.md",
            file_type="md",
            content_hash=hashlib.sha256(content).hexdigest(),
            status="uploaded",
            current_stage="uploaded",
            version_number=1,
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        doc.object_key = storage_service.build_object_key(
            user_id=self.user.id, document_id=doc.id, version_number=1, file_ext="md"
        )
        with io.BytesIO(content) as stream:
            storage_service.put_stream(doc.object_key, stream, content_type="text/markdown")
        self.db.add(doc)
        self.db.commit()
        return doc

    def _artifact(self, doc) -> DocumentParseArtifact:
        return (
            self.db.query(DocumentParseArtifact)
            .filter(DocumentParseArtifact.document_id == doc.id)
            .first()
        )

    def test_artifact_records_versions(self):
        doc = self._make_doc()
        run_parse(self.db, doc.id, user_id=self.user.id)
        artifact = self._artifact(doc)
        self.assertEqual(artifact.parser_version, document_pipeline.PARSER_VERSION)
        self.assertEqual(artifact.chunker_version, document_pipeline.chunker_version())
        self.assertEqual(artifact.content_hash, doc.content_hash)

    def test_parser_version_bump_triggers_reparse(self):
        doc = self._make_doc()
        run_parse(self.db, doc.id, user_id=self.user.id)
        with patch.object(document_pipeline, "PARSER_VERSION", "2"):
            result = run_parse(self.db, doc.id, user_id=self.user.id)
        self.assertEqual(result["status"], "success")  # 版本变化 → 不是 replay，重新解析
        artifact = self._artifact(doc)
        self.assertEqual(artifact.parser_version, "2")

    def test_chunker_version_bump_triggers_rechunk(self):
        doc = self._make_doc()
        run_parse(self.db, doc.id, user_id=self.user.id)
        run_chunk(self.db, doc.id)
        with patch.object(document_pipeline, "chunker_version", lambda: "rcs999-o0"):
            result = run_chunk(self.db, doc.id)
        self.assertEqual(result["status"], "success")  # 切分器版本变化 → 重新切分
        artifact = self._artifact(doc)
        self.assertEqual(artifact.chunker_version, "rcs999-o0")

    def test_embedding_model_bump_triggers_reindex(self):
        doc = self._make_doc()
        calls = []

        def recorder(document_id, chunks, *, user_id=None, knowledge_base_id=None):
            calls.append(1)
            return None

        run_parse(self.db, doc.id, user_id=self.user.id)
        run_chunk(self.db, doc.id)
        run_index(self.db, doc.id, user_id=self.user.id, index_chunks=recorder)
        self.assertEqual(len(calls), 1)

        with patch.object(get_settings(), "EMBEDDING_MODEL", "text-embedding-v3-new"):
            result = run_index(self.db, doc.id, user_id=self.user.id, index_chunks=recorder)
        self.assertEqual(result["status"], "success")  # 嵌入模型变化 → 重新索引，不跳过
        self.assertEqual(len(calls), 2)

    def test_same_input_reindex_skips(self):
        doc = self._make_doc()
        calls = []

        def recorder(document_id, chunks, *, user_id=None, knowledge_base_id=None):
            calls.append(1)
            return None

        run_parse(self.db, doc.id, user_id=self.user.id)
        run_chunk(self.db, doc.id)
        run_index(self.db, doc.id, user_id=self.user.id, index_chunks=recorder)
        run_index(self.db, doc.id, user_id=self.user.id, index_chunks=recorder)
        self.assertEqual(len(calls), 1)  # 输入未变 → 跳过重复索引


if __name__ == "__main__":
    unittest.main()
