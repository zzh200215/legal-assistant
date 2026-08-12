"""文档上传安全：流式读取、大小限制、真实 MIME 检测、MIME 白名单、病毒扫描、zip-bomb 防护。

- 上传必须流式：分块读取并边算 SHA-256，禁止把上传文件整体 read() 进内存。
- MIME 检测使用纯标准库 magic-byte 嗅探（项目未安装 python-magic），与扩展名交叉校验，
  不信任客户端 Content-Type。
- 病毒扫描抽象 ``VirusScanner``：默认“未配置扫描器”策略（不伪造扫描结果）；
  显式启用但无扫描器时 fail-closed 拒绝上传。
- zip-bomb 防护只读 zip 中央目录元数据（条目数 / 总解压大小 / 压缩比 / 嵌套 / 加密），
  绝不实际全量解压后再判断。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import get_settings

_HEAD_BYTES = 512
_ONE_MB = 1024 * 1024

# 纯 stdlib magic-byte 签名（顺序敏感：先长签名后短签名）
_MAGIC_SIGNATURES: list[tuple[str, bytes, str]] = [
    ("png", b"\x89PNG\r\n\x1a\n", "image/png"),
    ("pdf", b"%PDF", "application/pdf"),
    ("jpeg", b"\xff\xd8\xff", "image/jpeg"),
    ("bmp", b"BM", "image/bmp"),
    ("zip", b"PK\x03\x04", "application/zip"),
    ("zip", b"PK\x05\x06", "application/zip"),
    ("zip", b"PK\x07\x08", "application/zip"),
]

# webp: "RIFF" + 4 bytes size + "WEBP"
def _sniff_webp(head: bytes) -> bool:
    return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"


# 扩展名 -> MIME（白名单）
_EXTENSION_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "md": "text/markdown",
    "txt": "text/plain",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "bmp": "image/bmp",
    "webp": "image/webp",
}

_ZIP_BASED_EXTS = {"docx", "xlsx"}
_MAGIC_BINARY_EXTS = {"pdf", "png", "jpg", "jpeg", "bmp", "webp"}
_TEXT_EXTS = {"md", "txt"}
_ARCHIVE_SUFFIXES = (".zip", ".docx", ".xlsx", ".jar", ".apk", ".war", ".docm", ".xlsm")


class DocumentSecurityError(ValueError):
    """上传安全校验失败（携带错误码，供 API 层映射）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def allowed_extensions() -> set[str]:
    raw = get_settings().DOCUMENT_ALLOWED_EXTENSIONS or ""
    return {item.strip().lower().lstrip(".") for item in raw.split(",") if item.strip()}


def sniff_mime(head: bytes) -> tuple[str | None, str | None]:
    """根据文件头嗅探 (扩展名, MIME)；无法识别返回 (None, None)。"""
    if not head:
        return None, None
    if _sniff_webp(head):
        return "webp", _EXTENSION_MIME["webp"]
    for ext, magic, mime in _MAGIC_SIGNATURES:
        if head.startswith(magic):
            return ext, mime
    return None, None


def _looks_binary(head: bytes) -> bool:
    # 命中任一已知二进制签名即视为二进制；md/txt 期望为文本。
    if sniff_mime(head)[0] is not None:
        return True
    return b"\x00" in head[: max(1, min(len(head), 256))]


def detect_mime(head: bytes, filename: str) -> tuple[str, str]:
    """真实 MIME 检测：扩展名白名单 + magic-byte 交叉校验，返回 (ext, mime)。"""
    ext = Path(filename).suffix.lower().lstrip(".")
    allowed = allowed_extensions()
    if ext not in allowed:
        raise DocumentSecurityError(
            "DOCUMENT_TYPE_NOT_ALLOWED", f"不支持的文件类型：.{ext}（白名单：{', '.join(sorted(allowed))}）"
        )
    sniffed_ext, sniffed_mime = sniff_mime(head)

    # 顺序敏感：docx/xlsx 是 ZIP 容器（允许 PK 签名），先于 magic 完全匹配处理。
    if ext in _ZIP_BASED_EXTS:
        if sniffed_ext != "zip":
            raise DocumentSecurityError(
                "DOCUMENT_MIME_MISMATCH", f"文件头与扩展名不一致：.{ext} 应为 ZIP/OOXML 容器"
            )
        return ext, _EXTENSION_MIME[ext]
    if ext in _MAGIC_BINARY_EXTS:
        if ext == "webp":
            if not _sniff_webp(head):
                raise DocumentSecurityError("DOCUMENT_MIME_MISMATCH", f"文件头与 .{ext} 不一致（非 WebP）")
            return ext, _EXTENSION_MIME[ext]
        if sniffed_ext != ext:
            raise DocumentSecurityError(
                "DOCUMENT_MIME_MISMATCH", f"文件头与扩展名不一致：期望 .{ext}，实际为 .{sniffed_ext or '未知类型'}"
            )
        return ext, _EXTENSION_MIME[ext]
    # 文本类型：不得命中二进制签名
    if _looks_binary(head):
        raise DocumentSecurityError("DOCUMENT_MIME_MISMATCH", f".{ext} 应为文本文件，但文件头为二进制")
    return ext, _EXTENSION_MIME[ext]


def spool_upload_to_temp(source: BinaryIO, *, max_bytes: int) -> tuple[Path, int, str]:
    """流式把上传流落到本地临时文件，边读边算 SHA-256，并强制大小上限。

    返回 (temp_path, size, content_hash)。超限抛 DocumentSecurityError。
    """
    fd, tmp_path = tempfile.mkstemp(prefix="aibg-upload-", suffix=".tmp")
    hasher = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise DocumentSecurityError(
                        "DOCUMENT_TOO_LARGE",
                        f"文件超过大小限制（{max_bytes // _ONE_MB}MB）",
                    )
                out.write(chunk)
                hasher.update(chunk)
        return Path(tmp_path), size, hasher.hexdigest()
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def inspect_zip_safety(path: str | Path) -> None:
    """zip 容器（docx/xlsx）安全审查：只读中央目录，不实际解压。

    检查条目数 / 总解压大小 / 单条目压缩比 / 潜在嵌套归档 / 加密 / 未知压缩算法，
    任一超限抛 DocumentSecurityError。
    """
    settings = get_settings()
    max_entries = settings.DOCUMENT_ZIP_MAX_ENTRIES
    max_total = settings.DOCUMENT_ZIP_MAX_TOTAL_UNCOMPRESSED_MB * _ONE_MB
    max_ratio = settings.DOCUMENT_ZIP_MAX_COMPRESSION_RATIO
    max_nesting = settings.DOCUMENT_ZIP_MAX_NESTING

    try:
        with zipfile.ZipFile(str(path), "r") as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as exc:
        raise DocumentSecurityError("DOCUMENT_INVALID_ZIP", "文件损坏，不是有效的 ZIP/OOXML 容器") from exc

    if len(infos) > max_entries:
        raise DocumentSecurityError(
            "DOCUMENT_ZIP_BOMB", f"压缩包条目数超出限制（{len(infos)} > {max_entries}）"
        )

    total = sum(info.file_size for info in infos)
    if total > max_total:
        raise DocumentSecurityError(
            "DOCUMENT_ZIP_BOMB", f"压缩包解压总大小超出限制（{total // _ONE_MB}MB > {max_total // _ONE_MB}MB）"
        )

    has_content_types = False
    nested = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise DocumentSecurityError("DOCUMENT_ZIP_ENCRYPTED", "压缩包包含加密条目，拒绝处理")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise DocumentSecurityError("DOCUMENT_ZIP_UNSUPPORTED_COMPRESSION", "压缩包包含不支持的压缩算法")
        if info.file_size and info.compress_size and info.file_size / info.compress_size > max_ratio:
            raise DocumentSecurityError(
                "DOCUMENT_ZIP_BOMB", f"压缩包条目压缩比异常（{info.filename}）"
            )
        name = (info.filename or "").lower()
        if name == "[content_types].xml":
            has_content_types = True
        if name.endswith(_ARCHIVE_SUFFIXES):
            nested += 1

    if nested > max_nesting:
        raise DocumentSecurityError(
            "DOCUMENT_ZIP_BOMB", f"压缩包嵌套归档层数超出限制（{nested} > {max_nesting}）"
        )


def inspect_zip_safety_bytes(content: bytes) -> None:
    """内存字节版 zip 安全审查（供内存导入快路径使用）。"""
    fd, tmp_path = tempfile.mkstemp(prefix="aibg-upload-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content)
        inspect_zip_safety(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@dataclass(frozen=True)
class VirusScanResult:
    enabled: bool
    clean: bool
    note: str


class VirusScanner(Protocol):
    def scan(self, path: str | Path) -> VirusScanResult:
        ...


class NoopVirusScanner:
    """默认策略：未配置扫描器。不伪造扫描结果，明确标注未扫描。"""

    def scan(self, path: str | Path) -> VirusScanResult:
        return VirusScanResult(
            enabled=False,
            clean=True,
            note="未配置病毒扫描器（DOCUMENT_VIRUS_SCAN_ENABLED=false）",
        )


class ClamAVScanner:
    """ClamAV 守护进程扫描（可选依赖 clamd；未安装/未启用时走 Noop）。"""

    def __init__(self) -> None:
        try:
            import clamd  # type: ignore
        except ImportError as exc:  # pragma: no cover - 依赖未安装的分支
            raise DocumentSecurityError(
                "DOCUMENT_VIRUS_SCANNER_UNAVAILABLE",
                "病毒扫描已启用但未安装 clamd 依赖（pip install clamd）",
            ) from exc
        socket_path = get_settings().DOCUMENT_CLAMAV_SOCKET
        try:
            self._client = clamd.ClamdUnixSocket(socket_path)
            self._client.ping()
        except Exception as exc:  # noqa: BLE001
            raise DocumentSecurityError(
                "DOCUMENT_VIRUS_SCANNER_UNAVAILABLE",
                f"病毒扫描已启用但无法连接 ClamAV（{socket_path}）",
            ) from exc

    def scan(self, path: str | Path) -> VirusScanResult:
        result = self._client.scan(str(path))
        # clamd.scan 返回 {filename: ('OK'|'FOUND', signature)}
        verdict = next(iter(result.values()), None)
        found = verdict is not None and verdict[0] == "FOUND"
        return VirusScanResult(
            enabled=True,
            clean=not found,
            note=f"ClamAV: {verdict[1] if found else 'OK'}",
        )


def build_virus_scanner() -> VirusScanner:
    """构建病毒扫描器：默认 Noop；显式启用时要求真实扫描器，否则 fail-closed。"""
    if not get_settings().DOCUMENT_VIRUS_SCAN_ENABLED:
        return NoopVirusScanner()
    return ClamAVScanner()
