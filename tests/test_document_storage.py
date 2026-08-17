"""存储抽象：LocalStorageAdapter 流式读写/删除/元数据、object key 命名、选型与路径穿越防护。"""

import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.config import get_settings
from app.services.storage.storage_cloud_adapters import build_cloud_adapter
from app.services.storage.storage_service import (
    LocalStorageAdapter,
    StorageBackendUnavailable,
    build_storage_adapter,
    storage_service,
)


class LocalStorageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.adapter = LocalStorageAdapter(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_put_stream_returns_size_and_hash(self):
        source = io.BytesIO(b"hello world " * 1000)
        meta = self.adapter.put_stream("users/1/docs/1/v1/abc.md", source)
        self.assertEqual(meta["size"], len(b"hello world " * 1000))
        self.assertEqual(len(meta["content_hash"]), 64)

    def test_get_stream_reads_back_content(self):
        content = "# 合同\n付款条款".encode("utf-8") * 50
        self.adapter.put_stream("k.md", io.BytesIO(content), content_type="text/markdown")
        with self.adapter.get_stream("k.md") as stream:
            self.assertEqual(stream.read(), content)

    def test_delete_and_exists(self):
        self.adapter.put_stream("k.md", io.BytesIO(b"x"))
        self.assertTrue(self.adapter.exists("k.md"))
        self.adapter.delete("k.md")
        self.assertFalse(self.adapter.exists("k.md"))
        self.adapter.delete("k.md")  # 幂等

    def test_get_metadata(self):
        self.adapter.put_stream("k.md", io.BytesIO(b"12345"))
        meta = self.adapter.get_metadata("k.md")
        self.assertEqual(meta["size"], 5)
        self.assertEqual(self.adapter.get_metadata("missing"), {})

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            self.adapter.put_stream("../evil.md", io.BytesIO(b"x"))

    def test_materialize_to_local_returns_backed_path(self):
        key = "users/9/docs/3/v1/doc.md"
        self.adapter.put_stream(key, io.BytesIO(b"content"))
        # 注入本地 adapter 后，materialize 零拷贝返回对象路径
        original = storage_service._adapter
        storage_service._adapter = self.adapter
        try:
            path = storage_service.materialize_to_local(key)
            self.assertTrue(Path(path).is_file())
            self.assertEqual(path, Path(self.tmpdir) / key)
        finally:
            storage_service._adapter = original


class ObjectKeyNamingTests(unittest.TestCase):
    def test_build_object_key_isolates_tenant_user_doc_version(self):
        key = storage_service.build_object_key(
            user_id=7, document_id=42, version_number=2, file_ext="pdf"
        )
        self.assertTrue(key.startswith("users/7/docs/42/v2/"))
        self.assertTrue(key.endswith(".pdf"))
        self.assertNotEqual(
            storage_service.build_object_key(user_id=7, document_id=43, version_number=2, file_ext="pdf"),
            storage_service.build_object_key(user_id=7, document_id=42, version_number=2, file_ext="pdf"),
        )


class StorageAdapterFactoryTests(unittest.TestCase):
    def test_local_default(self):
        original = get_settings().STORAGE_PROVIDER
        with patch.object(get_settings(), "STORAGE_PROVIDER", "local"):
            adapter = build_storage_adapter()
        self.assertEqual(adapter.provider, "local")

    def test_unknown_provider_raises(self):
        with patch.object(get_settings(), "STORAGE_PROVIDER", "nfs"):
            with self.assertRaises(StorageBackendUnavailable):
                build_storage_adapter()

    def test_cloud_sdk_missing_raises_clear_error(self):
        # minio/boto3 未安装；oss2 已装但缺 endpoint 配置 → 都抛 StorageBackendUnavailable
        for provider in ("minio", "s3", "oss"):
            with self.assertRaises(StorageBackendUnavailable):
                build_cloud_adapter(provider)


if __name__ == "__main__":
    unittest.main()
