"""存储抽象（Storage Adapter）与兼容门面。

- 统一接口 ``StorageAdapter``：put_stream/get_stream/delete/exists/get_metadata，
  可选 generate_presigned_url；所有读写均为流式，禁止整体 read() 进内存。
- 后端由 ``STORAGE_PROVIDER`` 选择：local（默认，可用）| minio | s3 | oss。
  云后端 SDK 未安装时，选型处抛 ``StorageBackendUnavailable`` 并给出明确指引；
  业务层禁止散布 ``if storage_type`` 判断。
- 历史方法 ``save_bytes/read_bytes/to_data_url/ensure_dir/base_dir`` 保留为兼容门面，
  仍可用（旧调用方/测试不受影响）；新代码走流式接口。
- 数据库只保存 object_key 与内容元数据，不保存本地绝对路径/云 URL/二进制。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import get_settings


class StorageBackendUnavailable(RuntimeError):
    """所选存储后端不可用（SDK 未安装或配置缺失）。"""


class StorageAdapter(Protocol):
    """统一存储后端接口（流式）。"""

    provider: str

    def put_stream(self, key: str, source: BinaryIO, *, content_type: str | None = None) -> dict:
        """流式写入对象，返回 ``{"size": int, "content_hash": str}``。"""
        ...

    def get_stream(self, key: str) -> BinaryIO:
        """返回可读二进制流（调用方负责关闭）。"""
        ...

    def delete(self, key: str) -> None:
        ...

    def exists(self, key: str) -> bool:
        ...

    def get_metadata(self, key: str) -> dict:
        ...

    def generate_presigned_url(self, key: str, expires_in: int = 900) -> str:
        raise NotImplementedError


class LocalStorageAdapter:
    """本地磁盘流式实现：临时文件 + 原子 rename，chunk 写入、边写边算 hash。"""

    provider = "local"

    def __init__(self, base_dir: str | Path):
        self._root = Path(base_dir).resolve()

    def _path(self, key: str) -> Path:
        root = self._root
        target = (root / key).resolve()
        if target != root and not target.is_relative_to(root):
            raise ValueError(f"invalid object key: {key!r}")
        return target

    def put_stream(self, key: str, source: BinaryIO, *, content_type: str | None = None) -> dict:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        size = 0
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent), prefix=".stg-")
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
            os.replace(tmp_path, target)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return {"size": size, "content_hash": hasher.hexdigest()}

    def get_stream(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def delete(self, key: str) -> None:
        try:
            os.unlink(self._path(key))
        except FileNotFoundError:
            pass

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def get_metadata(self, key: str) -> dict:
        path = self._path(key)
        if not path.is_file():
            return {}
        stat = path.stat()
        return {"size": stat.st_size, "content_type": None, "mtime": stat.st_mtime}

    def generate_presigned_url(self, key: str, expires_in: int = 900) -> str:
        raise StorageBackendUnavailable("本地存储不支持预签名 URL，请配置对象存储后端")


class StorageService:
    """兼容门面：懒加载所选后端，保留历史方法，新增流式方法。"""

    def __init__(self) -> None:
        self._adapter: StorageAdapter | None = None

    @property
    def adapter(self) -> StorageAdapter:
        if self._adapter is None:
            self._adapter = build_storage_adapter()
        return self._adapter

    @property
    def provider(self) -> str:
        return self.adapter.provider

    def reset(self) -> None:
        """测试/配置变更后重置适配器缓存。"""
        self._adapter = None

    # ── 历史兼容方法（保留旧调用方/测试） ─────────────────────────────────────
    def base_dir(self) -> Path:
        return Path(get_settings().STORAGE_LOCAL_DIR)

    def ensure_dir(self, base_dir: Path) -> Path:
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir

    def save_bytes(self, *, base_dir: Path, filename: str, content: bytes) -> Path:
        directory = self.ensure_dir(base_dir)
        target = directory / filename
        with open(target, "wb") as f:
            f.write(content)
        return target

    def read_bytes(self, file_path: str | Path) -> bytes:
        with open(file_path, "rb") as f:
            return f.read()

    def to_data_url(self, file_path: str | Path, mime_type: str) -> str:
        import base64

        encoded = base64.b64encode(self.read_bytes(file_path)).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    # ── 新流式接口（委托给所选后端） ───────────────────────────────────────────
    def put_stream(self, key: str, source: BinaryIO, *, content_type: str | None = None) -> dict:
        return self.adapter.put_stream(key, source, content_type=content_type)

    def get_stream(self, key: str) -> BinaryIO:
        return self.adapter.get_stream(key)

    def delete(self, key: str) -> None:
        self.adapter.delete(key)

    def exists(self, key: str) -> bool:
        return self.adapter.exists(key)

    def get_metadata(self, key: str) -> dict:
        return self.adapter.get_metadata(key)

    def generate_presigned_url(self, key: str, expires_in: int = 900) -> str:
        return self.adapter.generate_presigned_url(key, expires_in=expires_in)

    def materialize_to_local(self, object_key: str) -> Path:
        """把对象落到本地临时文件并返回路径（供解析/水印等以路径为输入的功能）。

        本地后端直接返回对象文件路径（零拷贝）；云后端流式下载到系统临时目录。
        调用方应在用完后调用 ``discard_temp_path`` 清理（本地路径为 no-op）。
        """
        if self.provider == "local":
            path = self.adapter._path(object_key)  # noqa: SLF001 - 本地实现直接取文件路径
            if path.is_file():
                return path
        fd, tmp_path = tempfile.mkstemp(prefix="aibg-doc-", suffix=".tmp")
        os.close(fd)
        with self.get_stream(object_key) as src, open(tmp_path, "wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        return Path(tmp_path)

    def discard_temp_path(self, path: Path) -> None:
        """清理临时路径；本地对象路径不清理（属于受控存储）。"""
        if self.provider == "local":
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    def build_object_key(
        self,
        *,
        user_id: int,
        document_id: int,
        version_number: int,
        file_ext: str,
        token: str | None = None,
    ) -> str:
        """可预测、隔离租户/用户/文档版本的 object key 命名规则。

        ``users/{user_id}/docs/{document_id}/v{version_number}/{token}.{ext}``
        """
        ext = (file_ext or "bin").lstrip(".").lower() or "bin"
        leaf = token or uuid.uuid4().hex
        return f"users/{int(user_id)}/docs/{int(document_id)}/v{int(version_number)}/{leaf}.{ext}"


def _build_local_adapter() -> StorageAdapter:
    return LocalStorageAdapter(get_settings().STORAGE_LOCAL_DIR)


def build_storage_adapter() -> StorageAdapter:
    """按 ``STORAGE_PROVIDER`` 构建存储后端；SDK 缺失时给出明确报错。"""
    provider = (get_settings().STORAGE_PROVIDER or "local").strip().lower()
    if provider == "local":
        return _build_local_adapter()
    try:
        from app.services.storage_cloud_adapters import build_cloud_adapter

        return build_cloud_adapter(provider)
    except StorageBackendUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - 云适配器构建失败统一包装
        raise StorageBackendUnavailable(
            f"存储后端 {provider!r} 初始化失败：{type(exc).__name__}；请检查配置或安装对应 SDK"
        ) from exc


storage_service = StorageService()
