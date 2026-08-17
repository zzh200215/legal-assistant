"""P1-B 文件上传安全：伪造 MIME、超限、危险扩展名、路径穿越、压缩炸弹、
扫描失败/检出病毒、拒绝审计不含文件内容、存储最小权限。

- 单元层：document_security.detect_mime/secure_spool_file/storage 权限与路径穿越。
- API 层：文档上传、批量总上限、法源导入、合同审查拦截路径与审计落库。
"""

import io
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.legal_notifications import SecurityAuditEvent
from app.models.user import User
from app.services.documents.document_security import (
    DocumentSecurityError,
    build_virus_scanner,
    detect_mime,
    secure_spool_file,
    sniff_mime,
)
from app.services.storage.storage_service import LocalStorageAdapter

_PDF_HEAD = b"%PDF-1.7\n%test\n" + b"x" * 100
_PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"x" * 50
_TEXT = "# 测试标题\n正文内容".encode("utf-8")
_OLE2_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"x" * 64


def _make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


class DetectMimeCustomWhitelistTests(unittest.TestCase):
    def test_csv_text_allowed_with_custom_whitelist(self):
        # .csv 缺省不在文档白名单；业务端点自定义白名单时按文本类型校验
        self.assertEqual(detect_mime(b"title,source_type,content\n", "sources.csv", allowed={"csv"}), ("csv", "text/csv"))

    def test_csv_rejects_binary_content(self):
        with self.assertRaises(DocumentSecurityError) as ctx:
            detect_mime(_PDF_HEAD, "sources.csv", allowed={"csv"})
        self.assertEqual(ctx.exception.code, "DOCUMENT_MIME_MISMATCH")

    def test_xls_ole2_magic_validated(self):
        self.assertEqual(detect_mime(_OLE2_HEAD, "sheet.xls", allowed={"xls"}), ("xls", "application/vnd.ms-excel"))

    def test_xls_rejects_non_ole2_content(self):
        with self.assertRaises(DocumentSecurityError) as ctx:
            detect_mime(b"plain text,not ole2", "sheet.xls", allowed={"xls"})
        self.assertEqual(ctx.exception.code, "DOCUMENT_MIME_MISMATCH")

    def test_doc_ole2_magic_validated(self):
        self.assertEqual(detect_mime(_OLE2_HEAD, "contract.doc", allowed={"doc"}), ("doc", "application/msword"))

    def test_exe_always_rejected(self):
        with self.assertRaises(DocumentSecurityError) as ctx:
            detect_mime(b"MZ\x90\x00" + b"x" * 64, "evil.exe", allowed={"pdf"})
        self.assertEqual(ctx.exception.code, "DOCUMENT_TYPE_NOT_ALLOWED")

    def test_sniff_ole2_recognized(self):
        self.assertEqual(sniff_mime(_OLE2_HEAD)[0], None)  # OLE2 仅在 detect_mime 的 legacy 分支校验


class SecureSpoolFileTests(unittest.TestCase):
    def _file(self, content: bytes, name: str = "a.pdf"):
        return type("FakeFile", (), {"filename": name, "file": io.BytesIO(content)})()

    def test_success_returns_tuple_and_writes_temp(self):
        f = self._file(_PDF_HEAD + b"body", "a.pdf")
        path, ext, mime, size, digest = secure_spool_file(f, max_bytes=10 * 1024 * 1024)
        try:
            self.assertEqual(ext, "pdf")
            self.assertEqual(mime, "application/pdf")
            self.assertEqual(size, len(_PDF_HEAD) + 4)
            self.assertEqual(Path(path).read_bytes(), _PDF_HEAD + b"body")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_mime_mismatch_cleans_temp(self):
        f = self._file(_PNG_HEAD, "a.docx")
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            with self.assertRaises(DocumentSecurityError) as ctx:
                secure_spool_file(f, max_bytes=10 * 1024 * 1024)
            self.assertEqual(ctx.exception.code, "DOCUMENT_MIME_MISMATCH")
            self.assertFalse(list(Path(tmpdir).glob("aibg-upload-*")))

    def test_too_large_cleans_temp(self):
        f = self._file(_PDF_HEAD + b"x" * 2048, "a.pdf")
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            with self.assertRaises(DocumentSecurityError) as ctx:
                secure_spool_file(f, max_bytes=1024, allowed_exts={"pdf"})
            self.assertEqual(ctx.exception.code, "DOCUMENT_TOO_LARGE")
            self.assertFalse(list(Path(tmpdir).glob("aibg-upload-*")))

    def test_zip_bomb_rejected_on_docx(self):
        bomb = _make_zip([(f"e{i}.xml", b"x") for i in range(501)])
        f = self._file(bomb, "bomb.docx")
        with self.assertRaises(DocumentSecurityError) as ctx:
            secure_spool_file(f, max_bytes=10 * 1024 * 1024)
        self.assertEqual(ctx.exception.code, "DOCUMENT_ZIP_BOMB")

    def test_virus_scan_enabled_without_scanner_fails_closed(self):
        f = self._file(_PDF_HEAD + b"body", "a.pdf")
        with patch.object(get_settings(), "DOCUMENT_VIRUS_SCAN_ENABLED", True):
            with self.assertRaises(DocumentSecurityError) as ctx:
                secure_spool_file(f, max_bytes=10 * 1024 * 1024)
            self.assertEqual(ctx.exception.code, "DOCUMENT_VIRUS_SCANNER_UNAVAILABLE")

    def test_virus_found_rejected(self):
        class InfectedScanner:
            def scan(self, path):
                from app.services.documents.document_security import VirusScanResult
                return VirusScanResult(enabled=True, clean=False, note="ClamAV: Eicar-Test-Signature FOUND")

        f = self._file(_PDF_HEAD + b"body", "a.pdf")
        with patch("app.services.documents.document_security.build_virus_scanner", return_value=InfectedScanner()):
            with self.assertRaises(DocumentSecurityError) as ctx:
                secure_spool_file(f, max_bytes=10 * 1024 * 1024)
            self.assertEqual(ctx.exception.code, "DOCUMENT_VIRUS_FOUND")

    def test_custom_whitelist_used(self):
        f = self._file(b"title,content\n", "s.csv")
        path, ext, mime, _, _ = secure_spool_file(f, max_bytes=1024 * 1024, allowed_exts={"csv"})
        try:
            self.assertEqual((ext, mime), ("csv", "text/csv"))
        finally:
            Path(path).unlink(missing_ok=True)


class StorageHardeningTests(unittest.TestCase):
    def test_object_key_path_traversal_rejected(self):
        adapter = LocalStorageAdapter(tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            adapter._path("../../etc/passwd")
        with self.assertRaises(ValueError):
            adapter._path("users/1/../../../x.pdf")

    @unittest.skipIf(os.name != "posix", "POSIX 权限位仅在类 Unix 上生效")
    def test_put_stream_sets_minimal_permissions(self):
        tmpdir = tempfile.mkdtemp()
        adapter = LocalStorageAdapter(tmpdir)
        adapter.put_stream("users/1/docs/1/v1/leaf.pdf", io.BytesIO(b"pdf-bytes"), content_type="application/pdf")
        target = Path(tmpdir) / "users" / "1" / "docs" / "1" / "v1" / "leaf.pdf"
        mode = stat.S_IMODE(target.stat().st_mode)
        self.assertFalse(mode & stat.S_IXUSR, "文件不可执行")
        self.assertEqual(mode & 0o777, 0o600, "文件最小权限 0600")
        parent_mode = stat.S_IMODE(target.parent.stat().st_mode)
        self.assertEqual(parent_mode & 0o777, 0o700, "目录最小权限 0700")


class UploadApiSecurityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.TestingSessionLocal()

        self.admin = User(
            username="up_admin",
            email="up_admin@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
            organization_id=7,
        )
        self.member = User(
            username="up_member",
            email="up_member@example.com",
            hashed_password=hash_password("secret"),
            role="user",
            organization_id=7,
        )
        self.db.add_all([self.admin, self.member])
        self.db.commit()
        self.db.refresh(self.admin)
        self.db.refresh(self.member)

        def override_get_db():
            db = self.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.admin_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.admin.id})}"}
        self.member_headers = {"Authorization": f"Bearer {create_access_token({'sub': self.member.id})}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _audit_events(self) -> list[SecurityAuditEvent]:
        return self.db.query(SecurityAuditEvent).filter(SecurityAuditEvent.event_type == "document_upload").all()

    # ── 文档上传 ─────────────────────────────────────────────

    def test_document_upload_fake_mime_rejected_and_audited(self):
        # PNG 头伪装成 .pdf → DOCUMENT_MIME_MISMATCH + 审计（不含文件内容）
        files = {"file": ("fake.pdf", io.BytesIO(_PNG_HEAD + b"x" * 200), "application/pdf")}
        resp = self.client.post("/api/documents/upload", files=files, headers=self.member_headers)
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"]["code"], "DOCUMENT_MIME_MISMATCH")

        events = self._audit_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].result, "blocked")
        self.assertEqual(events[0].reason_code, "DOCUMENT_MIME_MISMATCH")
        meta = events[0].sanitized_metadata or ""
        self.assertIn("DOCUMENT_MIME_MISMATCH", meta)
        self.assertNotIn("PNG", meta)  # 不落文件内容/文件头
        self.assertNotIn(b"\x89PNG".hex(), meta)

    def test_document_upload_dangerous_extension_rejected(self):
        files = {"file": ("evil.sh", io.BytesIO(b"#!/bin/sh\nrm -rf /\n"), "text/x-shellscript")}
        resp = self.client.post("/api/documents/upload", files=files, headers=self.member_headers)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "DOCUMENT_TYPE_NOT_ALLOWED")

    def test_document_upload_too_large(self):
        with patch.object(get_settings(), "DOCUMENT_MAX_UPLOAD_MB", 1):
            files = {"file": ("big.pdf", io.BytesIO(_PDF_HEAD + b"y" * (2 * 1024 * 1024)), "application/pdf")}
            resp = self.client.post("/api/documents/upload", files=files, headers=self.member_headers)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "DOCUMENT_TOO_LARGE")
        self.assertEqual(len(self._audit_events()), 1)

    # ── 批量上传总上限 ────────────────────────────────────────

    def test_batch_upload_total_limit(self):
        with patch.object(get_settings(), "DOCUMENT_MAX_BATCH_TOTAL_MB", 1):
            files = [
                ("files", ("a.pdf", io.BytesIO(_PDF_HEAD + b"a" * (600 * 1024)), "application/pdf")),
                ("files", ("b.pdf", io.BytesIO(_PDF_HEAD + b"b" * (600 * 1024)), "application/pdf")),
            ]
            resp = self.client.post("/api/documents/batch-upload", files=files, headers=self.member_headers)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "DOCUMENT_BATCH_TOO_LARGE")
        self.assertEqual(len(self._audit_events()), 1)

    # ── 法源导入 ─────────────────────────────────────────────

    def test_source_import_fake_mime_rejected(self):
        files = {"file": ("sources.txt", io.BytesIO(_PDF_HEAD + b"x" * 100), "text/plain")}
        resp = self.client.post("/api/legal/sources/import", files=files, headers=self.admin_headers)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "DOCUMENT_TYPE_NOT_ALLOWED")
        self.assertEqual(len(self._audit_events()), 1)

    def test_source_import_xlsx_zip_bomb_rejected(self):
        bomb = _make_zip([(f"e{i}.xml", b"x") for i in range(501)])
        files = {"file": ("sources.xlsx", io.BytesIO(bomb), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        resp = self.client.post("/api/legal/sources/import", files=files, headers=self.admin_headers)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "DOCUMENT_ZIP_BOMB")
        self.assertEqual(len(self._audit_events()), 1)

    # ── 合同审查上传 ──────────────────────────────────────────

    def test_contract_review_upload_docx_zip_bomb_rejected(self):
        bomb = _make_zip([("word/document.xml", b"\x00" * (20 * 1024 * 1024))])
        files = {"file": ("contract.docx", io.BytesIO(bomb), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        resp = self.client.post("/api/legal/contract-reviews/upload", files=files, headers=self.member_headers)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "DOCUMENT_ZIP_BOMB")
        self.assertEqual(len(self._audit_events()), 1)

    def test_contract_review_upload_dangerous_extension_rejected(self):
        files = {"file": ("contract.exe", io.BytesIO(b"MZ\x90\x00" + b"x" * 100), "application/octet-stream")}
        resp = self.client.post("/api/legal/contract-reviews/upload", files=files, headers=self.member_headers)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "DOCUMENT_TYPE_NOT_ALLOWED")
        self.assertEqual(len(self._audit_events()), 1)


if __name__ == "__main__":
    unittest.main()