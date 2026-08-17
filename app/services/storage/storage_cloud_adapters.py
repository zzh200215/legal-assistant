"""可选云存储适配器：MinIO / AWS S3 / 阿里云 OSS。

- 本模块被 ``build_storage_adapter`` 按 ``STORAGE_PROVIDER`` 懒加载。
- 对应 SDK 未安装时，在适配器构造阶段抛 ``StorageBackendUnavailable`` 并给出安装指引，
  不阻塞本地后端。业务层无任何 ``if storage_type`` 判断。
- 所有上传/下载均为流式（上传从源流分块写入，下载返回可读流）。
"""

from __future__ import annotations

import hashlib
from typing import BinaryIO

from app.core.config import get_settings
from app.services.storage.storage_base import StorageBackendUnavailable, StorageAdapter

# 各可选 SDK 缺失时的统一报错文案
_SDK_HINTS = {
    "minio": "MinIO 后端需要安装可选依赖：pip install minio",
    "s3": "AWS S3 后端需要安装可选依赖：pip install boto3",
    "oss": "阿里云 OSS 后端需要安装可选依赖：pip install oss2",
}


def _require_sdk(provider: str, module_name: str):
    try:
        __import__(module_name)
    except ImportError as exc:
        raise StorageBackendUnavailable(_SDK_HINTS.get(provider, "可选 SDK 未安装")) from exc


def _stream_sha256(source: BinaryIO, write) -> dict:
    """边读边写边算 sha256（流式，禁止整体 read）。返回 size/content_hash。"""
    hasher = hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        write(chunk)
        hasher.update(chunk)
        size += len(chunk)
    return {"size": size, "content_hash": hasher.hexdigest()}


class MinIOStorageAdapter:
    provider = "minio"

    def __init__(self) -> None:
        _require_sdk("minio", "minio")
        from minio import Minio

        settings = get_settings()
        if not settings.STORAGE_MINIO_ENDPOINT:
            raise StorageBackendUnavailable("MinIO 后端需要配置 STORAGE_MINIO_ENDPOINT")
        self._client = Minio(
            settings.STORAGE_MINIO_ENDPOINT,
            access_key=settings.STORAGE_MINIO_ACCESS_KEY or None,
            secret_key=settings.STORAGE_MINIO_SECRET_KEY or None,
            secure=settings.STORAGE_MINIO_SECURE,
        )
        self._bucket = settings.STORAGE_MINIO_BUCKET
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from minio.error import S3Error

        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
        except S3Error as exc:
            raise StorageBackendUnavailable(f"MinIO bucket 就绪失败：{exc.code}") from exc

    def put_stream(self, key: str, source: BinaryIO, *, content_type: str | None = None) -> dict:
        # MinIO 支持 length=-1 表示未知长度流式上传
        self._client.put_object(self._bucket, key, source, length=-1, content_type=content_type)
        stat = self._client.stat_object(self._bucket, key)
        return {"size": stat.size, "content_hash": ""}

    def get_stream(self, key: str) -> BinaryIO:
        return self._client.get_object(self._bucket, key)

    def delete(self, key: str) -> None:
        try:
            self._client.remove_object(self._bucket, key)
        except Exception:  # noqa: BLE001 - 删除幂等
            pass

    def exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def get_metadata(self, key: str) -> dict:
        try:
            stat = self._client.stat_object(self._bucket, key)
            return {"size": stat.size, "content_type": stat.content_type, "mtime": stat.last_modified.timestamp()}
        except Exception:  # noqa: BLE001
            return {}

    def generate_presigned_url(self, key: str, expires_in: int = 900) -> str:
        return self._client.presigned_get_object(self._bucket, key, expires=expires_in)


class S3StorageAdapter:
    provider = "s3"

    def __init__(self) -> None:
        _require_sdk("s3", "boto3")
        import boto3

        settings = get_settings()
        if not settings.STORAGE_S3_BUCKET:
            raise StorageBackendUnavailable("AWS S3 后端需要配置 STORAGE_S3_BUCKET")
        kwargs = {
            "aws_access_key_id": settings.STORAGE_S3_ACCESS_KEY or None,
            "aws_secret_access_key": settings.STORAGE_S3_SECRET_KEY or None,
            "region_name": settings.STORAGE_S3_REGION or None,
        }
        if settings.STORAGE_S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = settings.STORAGE_S3_ENDPOINT_URL
        self._client = boto3.client("s3", **{k: v for k, v in kwargs.items() if v is not None})
        self._bucket = settings.STORAGE_S3_BUCKET

    def put_stream(self, key: str, source: BinaryIO, *, content_type: str | None = None) -> dict:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.upload_fileobj(Fileobj=source, Bucket=self._bucket, Key=key, ExtraArgs=extra)
        head = self._client.head_object(Bucket=self._bucket, Key=key)
        return {"size": head.get("ContentLength", 0), "content_hash": head.get("ETag", "").strip('"')}

    def get_stream(self, key: str) -> BinaryIO:
        return self._client.get_object(Bucket=self._bucket, Key=key)["Body"]

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception:  # noqa: BLE001
            pass

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def get_metadata(self, key: str) -> dict:
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
            return {"size": head.get("ContentLength", 0), "content_type": head.get("ContentType")}
        except Exception:  # noqa: BLE001
            return {}

    def generate_presigned_url(self, key: str, expires_in: int = 900) -> str:
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires_in
        )


class OSSStorageAdapter:
    provider = "oss"

    def __init__(self) -> None:
        _require_sdk("oss", "oss2")
        import oss2

        settings = get_settings()
        if not settings.STORAGE_OSS_ENDPOINT:
            raise StorageBackendUnavailable("阿里云 OSS 后端需要配置 STORAGE_OSS_ENDPOINT")
        auth = oss2.Auth(
            settings.STORAGE_OSS_ACCESS_KEY or "",
            settings.STORAGE_OSS_SECRET_KEY or "",
        )
        self._bucket = oss2.Bucket(auth, settings.STORAGE_OSS_ENDPOINT, settings.STORAGE_OSS_BUCKET)

    def put_stream(self, key: str, source: BinaryIO, *, content_type: str | None = None) -> dict:
        headers = {"Content-Type": content_type} if content_type else {}
        self._bucket.put_object(key, source, headers=headers)
        head = self._bucket.head_object(key)
        return {"size": head.content_length, "content_hash": head.etag.strip('"')}

    def get_stream(self, key: str) -> BinaryIO:
        return self._bucket.get_object(key)

    def delete(self, key: str) -> None:
        try:
            self._bucket.delete_object(key)
        except Exception:  # noqa: BLE001
            pass

    def exists(self, key: str) -> bool:
        return self._bucket.object_exists(key)

    def get_metadata(self, key: str) -> dict:
        try:
            head = self._bucket.head_object(key)
            return {"size": head.content_length, "content_type": head.content_type}
        except Exception:  # noqa: BLE001
            return {}

    def generate_presigned_url(self, key: str, expires_in: int = 900) -> str:
        import oss2

        return self._bucket.sign_url("GET", key, expires_in)


def build_cloud_adapter(provider: str) -> StorageAdapter:
    """按 provider 构造云存储适配器（SDK 缺失时抛 StorageBackendUnavailable）。"""
    if provider == "minio":
        return MinIOStorageAdapter()
    if provider == "s3":
        return S3StorageAdapter()
    if provider == "oss":
        return OSSStorageAdapter()
    raise StorageBackendUnavailable(f"未知存储后端：{provider!r}（支持 local/minio/s3/oss）")
