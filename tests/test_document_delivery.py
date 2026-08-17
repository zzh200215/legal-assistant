import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.document import Document
from app.models.operation_log import OperationLog
from app.models.user import User


class DocumentDeliveryApiTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=engine)
        Base.metadata.create_all(engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = Path(self.temp_dir.name)
        self.source = self.storage_root / "security-policy.md"
        self.source.write_text("# 访问策略\n\n仅限内部使用。", encoding="utf-8")

        db = self.Session()
        self.owner = User(username="owner", email="owner@example.com", hashed_password="x", full_name="张三")
        self.viewer = User(username="viewer", email="viewer@example.com", hashed_password="x")
        db.add_all([self.owner, self.viewer])
        db.commit()
        db.refresh(self.owner)
        db.refresh(self.viewer)
        self.owner_id = self.owner.id
        self.viewer_id = self.viewer.id
        self.document = Document(
            user_id=self.owner.id,
            title="访问策略",
            file_path=str(self.source),
            file_type="md",
            status="indexed",
            watermark_required=True,
        )
        db.add(self.document)
        db.commit()
        db.refresh(self.document)
        self.document_id = self.document.id
        db.close()

        def override_db():
            session = self.Session()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        self.owner_headers = {"Authorization": f"Bearer {create_access_token({'sub': str(self.owner_id)})}"}
        self.viewer_headers = {"Authorization": f"Bearer {create_access_token({'sub': str(self.viewer_id)})}"}
        self.storage_patcher = patch(
            "app.services.documents.document_delivery_service.storage_service.base_dir",
            return_value=self.storage_root,
        )
        self.storage_patcher.start()

    def tearDown(self):
        self.storage_patcher.stop()
        app.dependency_overrides.clear()
        self.temp_dir.cleanup()

    def test_owner_download_gets_watermarked_copy_and_audit_log(self):
        response = self.client.get(f"/api/documents/{self.document_id}/download", headers=self.owner_headers)

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("内部受控副本", response.content.decode("utf-8"))
        self.assertIn("下载人：张三", response.content.decode("utf-8"))
        generated_dir = self.storage_root / "governed_downloads"
        self.assertFalse(generated_dir.exists() and any(generated_dir.iterdir()))

        db = self.Session()
        entry = db.query(OperationLog).filter(OperationLog.action == "document_downloaded").one()
        self.assertEqual(entry.target_id, self.document_id)
        self.assertIn("watermark_applied=True", entry.detail)
        db.close()

    def test_download_policy_blocks_and_cannot_be_changed_by_unrelated_user(self):
        forbidden = self.client.patch(
            f"/api/documents/{self.document_id}/download-policy",
            headers=self.viewer_headers,
            json={"download_enabled": False},
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(forbidden.json()["error"]["code"], "DOCUMENT_DOWNLOAD_POLICY_FORBIDDEN")

        disabled = self.client.patch(
            f"/api/documents/{self.document_id}/download-policy",
            headers=self.owner_headers,
            json={"download_enabled": False},
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.json()["data"]["download_enabled"])

        blocked = self.client.get(f"/api/documents/{self.document_id}/download", headers=self.owner_headers)
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["error"]["code"], "DOCUMENT_DOWNLOAD_DISABLED")

    def test_private_document_is_not_downloadable_by_unrelated_user(self):
        response = self.client.get(f"/api/documents/{self.document_id}/download", headers=self.viewer_headers)
        self.assertEqual(response.status_code, 404)
