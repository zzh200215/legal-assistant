"""P2 多链接聚合页：aggregate_case 链接自动聚合该案全部已发布客户可见内容。"""
import hashlib
import json
import secrets
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

import app.models  # noqa: F401
from app.core.auth import hash_password, create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember
from app.models.legal import LegalCase
from app.models.document import Document
from app.models.legal_portal import (
    LegalPortalLink, LegalPortalLinkItem, LegalCaseProgressUpdate,
)
from fastapi.testclient import TestClient


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class PortalAggregateTests(unittest.TestCase):
    """聚合开关：aggregate_case=1 的链接展示该案全部已发布客户可见内容并放开文书下载"""

    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="AggOrg", code="AGGR")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id

        self.admin = User(
            username="agg_admin",
            email="agg@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.admin)
        self.db.flush()
        self.db.add(OrganizationMember(
            organization_id=org.id, user_id=self.admin.id, legal_role="admin"))

        self.case = LegalCase(
            title="聚合案件", case_type="other",
            organization_id=org.id, user_id=self.admin.id,
        )
        self.db.add(self.case)
        self.db.flush()

        self.pub_update = LegalCaseProgressUpdate(
            case_id=self.case.id, organization_id=org.id,
            title="已发布进度", body="正文", visibility="client_visible", status="published",
            created_by=self.admin.id,
        )
        self.db.add(self.pub_update)
        self.internal_update = LegalCaseProgressUpdate(
            case_id=self.case.id, organization_id=org.id,
            title="内部进度", body="正文", visibility="internal", status="published",
            created_by=self.admin.id,
        )
        self.db.add(self.internal_update)
        self.draft_update = LegalCaseProgressUpdate(
            case_id=self.case.id, organization_id=org.id,
            title="草稿进度", body="正文", visibility="client_visible", status="draft",
            created_by=self.admin.id,
        )
        self.db.add(self.draft_update)
        self.db.flush()

        self.doc_case = self._make_doc("案件文书", enabled=True)
        self.doc_disabled = self._make_doc("禁用文书", enabled=False)
        self.doc_other_case = Document(
            user_id=self.admin.id, organization_id=org.id,
            title="其他案件文书", file_path="/tmp/x.txt", file_type="txt",
            download_enabled=True, metadata_json=json.dumps({"case_id": 999999}),
        )
        self.db.add(self.doc_other_case)
        self.db.commit()

        self.tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        self.tmp.write(b"portal doc")
        self.tmp.close()

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        token = create_access_token({"sub": str(self.admin.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def _make_doc(self, title: str, enabled: bool) -> Document:
        doc = Document(
            user_id=self.admin.id, organization_id=self.org_id,
            title=title, file_path="/tmp/x.txt", file_type="txt",
            download_enabled=enabled,
            metadata_json=json.dumps({"case_id": self.case.id}),
        )
        self.db.add(doc)
        return doc

    def _make_link(self, *, aggregate_case: int = 0, items: list | None = None,
                   reqver: int = 0) -> tuple[str, LegalPortalLink]:
        raw = secrets.token_urlsafe(32)
        link = LegalPortalLink(
            organization_id=self.org_id,
            case_id=self.case.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:8],
            client_email="c@example.com",
            is_permanent=1,
            require_email_verification=reqver,
            aggregate_case=aggregate_case,
            created_by=self.admin.id,
            status="active",
        )
        self.db.add(link)
        self.db.flush()
        for it in items or []:
            self.db.add(LegalPortalLinkItem(
                portal_link_id=link.id, item_type=it["item_type"], item_id=it["item_id"]))
        self.db.commit()
        self.db.refresh(link)
        return raw, link

    def _download(self, raw_token: str, doc_id: int):
        with patch(
            "app.services.documents.document_delivery_service.document_delivery_service.prepare_download",
            return_value={"path": self.tmp.name, "filename": "doc.txt",
                          "media_type": "text/plain", "temporary": False},
        ):
            return self.client.get(f"/api/legal/portal/{raw_token}/documents/{doc_id}/download")

    def test_aggregate_link_returns_all_published_case_content(self):
        raw, _ = self._make_link(aggregate_case=1)
        resp = self.client.get(f"/api/legal/portal/{raw}/content")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        progress = [u["title"] for u in data["progress_updates"]]
        docs = [d["title"] for d in data["documents"]]
        self.assertIn("已发布进度", progress)
        self.assertNotIn("内部进度", progress)
        self.assertNotIn("草稿进度", progress)
        self.assertIn("案件文书", docs)
        self.assertNotIn("禁用文书", docs)
        self.assertNotIn("其他案件文书", docs)

    def test_non_aggregate_link_returns_only_its_items(self):
        raw, _ = self._make_link(aggregate_case=0, items=[
            {"item_type": "progress_update", "item_id": self.pub_update.id},
        ])
        resp = self.client.get(f"/api/legal/portal/{raw}/content")
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()["data"]
        self.assertEqual([u["title"] for u in data["progress_updates"]], ["已发布进度"])
        self.assertEqual(data["documents"], [])

    def test_aggregate_link_allows_download_of_case_doc_not_in_items(self):
        raw, _ = self._make_link(aggregate_case=1)
        resp = self._download(raw, self.doc_case.id)
        self.assertEqual(resp.status_code, 200, resp.text[:200])

    def test_non_aggregate_link_download_requires_item(self):
        raw, _ = self._make_link(aggregate_case=0)
        resp = self._download(raw, self.doc_case.id)
        self.assertEqual(resp.status_code, 404)

    def test_aggregate_link_rejects_other_case_doc(self):
        raw, _ = self._make_link(aggregate_case=1)
        resp = self._download(raw, self.doc_other_case.id)
        self.assertEqual(resp.status_code, 404)

    def test_create_aggregate_link_persists_flag(self):
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_id}/cases/{self.case.id}/portal-links",
            headers=self.headers,
            json={"client_email": "c@t.com", "expires_days": 7,
                  "aggregate_case": 1, "require_email_verification": 1, "items": []},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        link_id = resp.json()["data"]["link"]["id"]
        from app.models.legal_portal import LegalPortalLink as LPL
        link = self.db.query(LPL).filter(LPL.id == link_id).first()
        self.assertEqual(link.aggregate_case, 1)

    def test_create_aggregate_link_requires_email_verification(self):
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_id}/cases/{self.case.id}/portal-links",
            headers=self.headers,
            json={"client_email": "c@t.com", "expires_days": 7,
                  "aggregate_case": 1, "require_email_verification": 0, "items": []},
        )
        self.assertEqual(resp.status_code, 400, resp.text)


if __name__ == "__main__":
    unittest.main()
