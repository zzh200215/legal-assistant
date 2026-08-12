"""上传安全：真实 MIME 检测与白名单、流式大小限制、zip-bomb 防护、病毒扫描策略。"""

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.core.config import get_settings
from app.services.document_security import (
    DocumentSecurityError,
    build_virus_scanner,
    detect_mime,
    inspect_zip_safety,
    inspect_zip_safety_bytes,
    sniff_mime,
    spool_upload_to_temp,
)

_PDF_HEAD = b"%PDF-1.7\n%test\n" + b"x" * 100
_PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"x" * 100
_ZIP_HEAD = b"PK\x03\x04" + b"x" * 100
_WEBP_HEAD = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"x" * 50


def _make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buffer.getvalue()


class MimeDetectionTests(unittest.TestCase):
    def test_sniff_known_magics(self):
        self.assertEqual(sniff_mime(_PDF_HEAD)[0], "pdf")
        self.assertEqual(sniff_mime(_PNG_HEAD)[0], "png")
        self.assertEqual(sniff_mime(_ZIP_HEAD)[0], "zip")
        self.assertEqual(sniff_mime(_WEBP_HEAD)[0], "webp")

    def test_detect_mime_valid(self):
        self.assertEqual(detect_mime(_PDF_HEAD, "a.pdf"), ("pdf", "application/pdf"))
        self.assertEqual(detect_mime(_PNG_HEAD, "a.png"), ("png", "image/png"))
        self.assertEqual(detect_mime(_ZIP_HEAD, "a.docx"), ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))
        self.assertEqual(detect_mime("# title\n正文".encode("utf-8"), "a.md"), ("md", "text/markdown"))

    def test_rejects_unknown_extension(self):
        with self.assertRaises(DocumentSecurityError) as ctx:
            detect_mime(b"anything", "a.exe")
        self.assertEqual(ctx.exception.code, "DOCUMENT_TYPE_NOT_ALLOWED")

    def test_rejects_mime_mismatch(self):
        # PDF 伪装成 md / txt（文本类型不得命中二进制签名）
        with self.assertRaises(DocumentSecurityError) as ctx:
            detect_mime(_PDF_HEAD, "a.md")
        self.assertEqual(ctx.exception.code, "DOCUMENT_MIME_MISMATCH")
        # PNG 伪装成 docx
        with self.assertRaises(DocumentSecurityError) as ctx:
            detect_mime(_PNG_HEAD, "a.docx")
        self.assertEqual(ctx.exception.code, "DOCUMENT_MIME_MISMATCH")
        # 文本伪装成 png
        with self.assertRaises(DocumentSecurityError) as ctx:
            detect_mime(b"not a real png" * 10, "a.png")
        self.assertEqual(ctx.exception.code, "DOCUMENT_MIME_MISMATCH")


class StreamingUploadTests(unittest.TestCase):
    def test_spool_returns_size_and_sha256(self):
        content = "# title\n正文内容".encode("utf-8") * 100
        stream = io.BytesIO(content)
        path, size, digest = spool_upload_to_temp(stream, max_bytes=10 * 1024 * 1024)
        try:
            import hashlib

            self.assertEqual(size, len(content))
            self.assertEqual(digest, hashlib.sha256(content).hexdigest())
            self.assertEqual(Path(path).read_bytes(), content)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_spool_enforces_size_limit(self):
        stream = io.BytesIO(b"x" * 2048)
        with self.assertRaises(DocumentSecurityError) as ctx:
            spool_upload_to_temp(stream, max_bytes=1024)
        self.assertEqual(ctx.exception.code, "DOCUMENT_TOO_LARGE")
        # 超限后临时文件被清理
        leftovers = [p for p in Path(tempfile.gettempdir()).glob("aibg-upload-*.tmp")]
        self.assertFalse(leftovers)


class ZipBombTests(unittest.TestCase):
    def test_valid_zip_passes(self):
        content = _make_zip([("[Content_Types].xml", b"<Types/>"), ("word/document.xml", b"<w:document/>")])
        inspect_zip_safety_bytes(content)

    def test_too_many_entries_rejected(self):
        entries = [(f"e{i}.xml", b"x") for i in range(501)]
        content = _make_zip(entries)
        with self.assertRaises(DocumentSecurityError) as ctx:
            inspect_zip_safety_bytes(content)
        self.assertEqual(ctx.exception.code, "DOCUMENT_ZIP_BOMB")

    def test_compression_ratio_rejected(self):
        # 20MB 全零压缩比极高，但总解压大小低于总上限 → 仅压缩比触发
        content = _make_zip([("big.xml", b"\x00" * (20 * 1024 * 1024))])
        with self.assertRaises(DocumentSecurityError) as ctx:
            inspect_zip_safety_bytes(content)
        self.assertEqual(ctx.exception.code, "DOCUMENT_ZIP_BOMB")

    def test_nested_archives_rejected(self):
        content = _make_zip(
            [("nested1.docx", b"a"), ("nested2.xlsx", b"b"), ("nested3.zip", b"c"), ("real.xml", b"d")]
        )
        with self.assertRaises(DocumentSecurityError) as ctx:
            inspect_zip_safety_bytes(content)
        self.assertEqual(ctx.exception.code, "DOCUMENT_ZIP_BOMB")

    def test_encrypted_entry_rejected(self):
        # zipfile 写回时会重置加密位，直接构造一个带加密 flag 的 ZipInfo 供审查。
        fake_info = type(
            "FakeInfo",
            (),
            {
                "flag_bits": 0x1,
                "compress_type": zipfile.ZIP_DEFLATED,
                "file_size": 10,
                "compress_size": 5,
                "filename": "secret.txt",
            },
        )
        with patch.object(zipfile.ZipFile, "infolist", return_value=[fake_info]):
            with self.assertRaises(DocumentSecurityError) as ctx:
                inspect_zip_safety_bytes(_make_zip([("a.txt", b"x")]))
        self.assertEqual(ctx.exception.code, "DOCUMENT_ZIP_ENCRYPTED")

    def test_invalid_zip_rejected(self):
        with self.assertRaises(DocumentSecurityError) as ctx:
            inspect_zip_safety_bytes(b"this is not a zip" * 10)
        self.assertEqual(ctx.exception.code, "DOCUMENT_INVALID_ZIP")

    def test_inspect_only_reads_central_directory(self):
        # 只读中央目录即可判定，绝不打开/解压条目（patch ZipFile.open 触发即失败）。
        content = _make_zip([("a.txt", b"hello")])
        with patch.object(zipfile.ZipFile, "open", side_effect=AssertionError("must not extract members")):
            inspect_zip_safety_bytes(content)


class VirusScannerTests(unittest.TestCase):
    def test_default_noop_scanner_not_enabled(self):
        with patch.object(get_settings(), "DOCUMENT_VIRUS_SCAN_ENABLED", False):
            scanner = build_virus_scanner()
        result = scanner.scan("/tmp/anything")
        self.assertFalse(result.enabled)
        self.assertTrue(result.clean)
        self.assertIn("未配置", result.note)

    def test_enabled_without_clamd_fails_closed(self):
        with patch.object(get_settings(), "DOCUMENT_VIRUS_SCAN_ENABLED", True):
            with self.assertRaises(DocumentSecurityError) as ctx:
                build_virus_scanner()
        self.assertEqual(ctx.exception.code, "DOCUMENT_VIRUS_SCANNER_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
