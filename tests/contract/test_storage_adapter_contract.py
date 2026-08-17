"""契约测试：存储适配器（StorageAdapter 协议）——本系统承诺的调用协议。

被测对象：app/services/storage/ 下 StorageAdapter 协议、三个云适配器
（MinIO/S3/OSS）的 SDK 调用面、流式哈希助手与适配器工厂。
替身：fake SDK 客户端（记录调用参数），不依赖真实对象存储。
契约点：上传/下载/删除/存在性/元数据/签名 URL 的调用签名与返回结构。
"""

import hashlib
import io
import unittest
from types import SimpleNamespace

from app.services.storage.storage_base import StorageBackendUnavailable
from app.services.storage.storage_cloud_adapters import (
    MinIOStorageAdapter,
    OSSStorageAdapter,
    S3StorageAdapter,
    _stream_sha256,
    build_cloud_adapter,
)
from app.services.storage.storage_service import LocalStorageAdapter


def _bare(adapter_cls, **attrs):
    """绕过 __init__（SDK 缺失）直接构造实例并注入 fake 客户端。"""
    obj = object.__new__(adapter_cls)
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj


class StorageAdapterProtocolContractTests(unittest.TestCase):
    """协议一致性：所有适配器实现同一方法面。"""

    ADAPTERS = (MinIOStorageAdapter, S3StorageAdapter, OSSStorageAdapter, LocalStorageAdapter)
    REQUIRED = {
        "provider",
        "put_stream",
        "get_stream",
        "delete",
        "exists",
        "get_metadata",
        "generate_presigned_url",
    }

    def test_all_adapters_implement_protocol(self):
        for cls in self.ADAPTERS:
            missing = self.REQUIRED - set(dir(cls))
            self.assertFalse(missing, f"{cls.__name__} 缺少协议方法: {missing}")

    def test_local_adapter_provider_name(self):
        self.assertEqual(LocalStorageAdapter.provider, "local")


class StreamSha256ContractTests(unittest.TestCase):
    def test_streams_in_chunks_and_returns_size_hash(self):
        payload = b"x" * (2 * 1024 * 1024 + 17)  # 多块（块大小 1MB）
        writes = []

        def _write(chunk):
            writes.append(chunk)

        result = _stream_sha256(io.BytesIO(payload), _write)
        self.assertEqual(result["size"], len(payload))
        self.assertEqual(result["content_hash"], hashlib.sha256(payload).hexdigest())
        self.assertGreater(len(writes), 1)  # 确为流式分块

    def test_empty_stream(self):
        result = _stream_sha256(io.BytesIO(b""), lambda chunk: None)
        self.assertEqual(result, {"size": 0, "content_hash": hashlib.sha256(b"").hexdigest()})


class MinIOAdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.client = SimpleNamespace(
            put_object=lambda *a, **k: self.calls.append(("put_object", a, k)),
            stat_object=lambda *a, **k: self.calls.append(("stat_object", a, k)) or SimpleNamespace(size=7, content_type="text/plain", last_modified=SimpleNamespace(timestamp=lambda: 1.0)),
            get_object=lambda *a, **k: self.calls.append(("get_object", a, k)) or io.BytesIO(b"data"),
            remove_object=lambda *a, **k: self.calls.append(("remove_object", a, k)),
            bucket_exists=lambda *a, **k: True,
            presigned_get_object=lambda *a, **k: self.calls.append(("presigned", a, k)) or "http://minio/signed",
        )
        self.adapter = _bare(MinIOStorageAdapter, _client=self.client, _bucket="bucket-a")

    def test_put_stream_signature(self):
        source = io.BytesIO(b"hello")
        result = self.adapter.put_stream("k1", source, content_type="text/plain")
        op, args, kwargs = self.calls[0]
        self.assertEqual(op, "put_object")
        self.assertEqual(args[0:2], ("bucket-a", "k1"))
        self.assertEqual(kwargs["length"], -1)
        self.assertEqual(kwargs["content_type"], "text/plain")
        self.assertEqual(result, {"size": 7, "content_hash": ""})

    def test_get_stream_and_metadata_and_presigned(self):
        self.assertEqual(self.adapter.get_stream("k1").read(), b"data")
        self.assertEqual(self.adapter.exists("k1"), True)
        meta = self.adapter.get_metadata("k1")
        self.assertEqual(meta["size"], 7)
        url = self.adapter.generate_presigned_url("k1", expires_in=300)
        self.assertEqual(url, "http://minio/signed")
        self.assertEqual(self.calls[-1][2]["expires"], 300)

    def test_delete_is_idempotent(self):
        self.adapter.delete("k1")
        # SDK 抛异常时删除静默
        self.client.remove_object = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gone"))
        self.adapter.delete("k1")  # 不抛


class S3AdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.client = SimpleNamespace(
            upload_fileobj=lambda **k: self.calls.append(("upload", k)),
            head_object=lambda **k: self.calls.append(("head", k)) or {"ContentLength": 9, "ContentType": "text/plain", "ETag": '"abc"'},
            get_object=lambda **k: self.calls.append(("get", k)) or {"Body": io.BytesIO(b"payload")},
            delete_object=lambda **k: self.calls.append(("delete", k)),
            generate_presigned_url=lambda *a, **k: self.calls.append(("presign", a, k)) or "http://s3/signed",
        )
        self.adapter = _bare(S3StorageAdapter, _client=self.client, _bucket="bucket-s3")

    def test_put_stream_signature(self):
        result = self.adapter.put_stream("k1", io.BytesIO(b"123456789"), content_type="text/plain")
        _, kwargs = self.calls[0]
        self.assertEqual(kwargs["Bucket"], "bucket-s3")
        self.assertEqual(kwargs["Key"], "k1")
        self.assertEqual(kwargs["ExtraArgs"], {"ContentType": "text/plain"})
        self.assertEqual(result, {"size": 9, "content_hash": "abc"})

    def test_get_and_presigned(self):
        self.assertEqual(self.adapter.get_stream("k1").read(), b"payload")
        url = self.adapter.generate_presigned_url("k1", expires_in=600)
        self.assertEqual(url, "http://s3/signed")
        op, args, kwargs = self.calls[-1]
        self.assertEqual(kwargs["ExpiresIn"], 600)


class OSSAdapterContractTests(unittest.TestCase):
    def setUp(self):
        import sys

        # generate_presigned_url 方法内 import oss2：契约测试 stub 掉 SDK 模块
        sys.modules.setdefault("oss2", SimpleNamespace())
        self.calls = []
        self.bucket = SimpleNamespace(
            put_object=lambda *a, **k: self.calls.append(("put", a, k)),
            head_object=lambda *a, **k: self.calls.append(("head", a, k)) or SimpleNamespace(content_length=5, content_type="text/plain", etag='"oss-etag"'),
            get_object=lambda *a, **k: self.calls.append(("get", a, k)) or io.BytesIO(b"12345"),
            object_exists=lambda *a, **k: self.calls.append(("exists", a, k)) or False,
            delete_object=lambda *a, **k: self.calls.append(("delete", a, k)),
            sign_url=lambda *a, **k: self.calls.append(("sign", a, k)) or "http://oss/signed",
        )
        self.adapter = _bare(OSSStorageAdapter, _bucket=self.bucket)

    def test_put_stream_signature(self):
        result = self.adapter.put_stream("k1", io.BytesIO(b"12345"), content_type="text/plain")
        op, args, kwargs = self.calls[0]
        self.assertEqual(op, "put")
        self.assertEqual(args[0], "k1")
        self.assertIsInstance(args[1], io.BytesIO)
        self.assertEqual(kwargs, {"headers": {"Content-Type": "text/plain"}})
        self.assertEqual(result, {"size": 5, "content_hash": "oss-etag"})

    def test_exists_and_presigned(self):
        self.assertFalse(self.adapter.exists("k1"))
        url = self.adapter.generate_presigned_url("k1", expires_in=900)
        self.assertEqual(url, "http://oss/signed")
        op, args, kwargs = self.calls[-1]
        self.assertEqual(args, ("GET", "k1", 900))


class StorageFactoryContractTests(unittest.TestCase):
    def test_unknown_provider_raises(self):
        with self.assertRaises(StorageBackendUnavailable):
            build_cloud_adapter("ftp")

    def test_sdk_missing_raises_with_hint(self):
        # 未安装 SDK 时构造阶段抛 StorageBackendUnavailable（不影响本地启动）
        with self.assertRaises(StorageBackendUnavailable):
            MinIOStorageAdapter()


if __name__ == "__main__":
    unittest.main()
