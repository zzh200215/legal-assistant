import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.services.connector_service import connector_service
from app.services.enterprise_connector_service import enterprise_connector_service
from app.services.mailbox_service import mailbox_service


class FakeResponse:
    def __init__(self, *, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def json(self):  # noqa: F811 - method name shadows stdlib json; called via response.json()
        return self._payload

    def raise_for_status(self):
        return None


class FakeGraphClient:
    def __init__(self, *args, **kwargs):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "root/delta" in url:
            return FakeResponse(payload={
                "value": [
                    {
                        "id": "file-1",
                        "name": "销售制度.docx",
                        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                        "@microsoft.graph.downloadUrl": "https://download.example.com/file-1",
                        "webUrl": "https://contoso.sharepoint.com/doc/file-1",
                        "lastModifiedDateTime": "2026-07-18T10:00:00Z",
                        "eTag": "etag-1",
                    },
                    {"id": "folder-1", "name": "归档", "folder": {}},
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=next",
            })
        if "download.example.com" in url:
            return FakeResponse(content=b"graph-file-content")
        raise AssertionError(f"unexpected URL: {url}")


class FakeRestClient:
    def __init__(self, *args, **kwargs):
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return FakeResponse(payload={
            "data": {
                "items": [
                    {"id": "c-1", "name": "华北汽车", "stage": "proposal", "amount": 320000, "updated_at": "2026-07-18T08:00:00Z"},
                    {"id": "c-2", "name": "华东新能源", "stage": "won", "amount": 420000, "updated_at": "2026-07-18T09:00:00Z"},
                ]
            }
        })


class FakeTokenResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):  # noqa: F811 - method name shadows stdlib json; called via response.json()
        return self.payload


class EnterpriseConnectorServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        Base.metadata.create_all(bind=engine)
        self.admin = User(username="enterprise_admin", email="admin@example.com", role="admin")
        self.db.add(self.admin)
        self.db.commit()
        self.db.refresh(self.admin)

    def tearDown(self):
        self.db.close()

    def test_graph_connector_encrypts_token_and_builds_incremental_file_batch(self):
        connector = connector_service.create_enterprise_connector(
            db=self.db,
            user=self.admin,
            connector_type="ms_graph_onedrive",
            name="销售 OneDrive",
            config={"drive_id": "drive-01", "max_files": 10, "permission_scope": "org"},
            credentials={"access_token": "graph-secret-token"},
        )
        self.assertNotIn("graph-secret-token", connector.config_json)
        self.assertNotIn("graph-secret-token", connector.credential_ciphertext)
        self.assertEqual(mailbox_service.decrypt_credentials(connector.credential_ciphertext)["access_token"], "graph-secret-token")

        with patch("app.services.enterprise_connector_service.httpx.Client", FakeGraphClient):
            batch = enterprise_connector_service.build_batch(connector)

        self.assertEqual(batch.scanned_count, 2)
        self.assertEqual(batch.source, "Microsoft Graph / OneDrive")
        self.assertEqual(batch.sync_cursor["delta_link"], "https://graph.microsoft.com/v1.0/me/drive/root/delta?token=next")
        self.assertEqual(batch.documents[0]["title"], "销售制度.docx")
        self.assertEqual(batch.documents[0]["file_bytes"], b"graph-file-content")
        self.assertEqual(batch.documents[0]["metadata"]["remote_id"], "file-1")

    def test_rest_connector_maps_records_to_traceable_knowledge_entries(self):
        connector = connector_service.create_enterprise_connector(
            db=self.db,
            user=self.admin,
            connector_type="crm_rest",
            name="HubSpot CRM",
            config={
                "endpoint": "https://crm.example.com/v3",
                "resource_path": "/customers",
                "items_path": "data.items",
                "entity_name": "客户商机",
                "title_field": "name",
                "id_field": "id",
                "content_fields": ["name", "stage", "amount"],
                "cursor_field": "updated_at",
                "cursor_param": "updated_after",
            },
            credentials={"bearer_token": "crm-token"},
        )
        with patch("app.services.enterprise_connector_service.httpx.Client", FakeRestClient):
            batch = enterprise_connector_service.build_batch(connector)

        self.assertEqual(batch.scanned_count, 2)
        self.assertEqual(batch.source, "CRM REST API")
        self.assertEqual(batch.sync_cursor["value"], "2026-07-18T09:00:00Z")
        self.assertEqual(batch.documents[0]["title"], "客户商机-华北汽车")
        self.assertIn("- amount: 320000", batch.documents[0]["content"])
        self.assertEqual(batch.documents[0]["metadata"]["remote_id"], "c-1")

    def test_enterprise_configuration_rejects_tokens_in_plain_config(self):
        with self.assertRaisesRegex(ValueError, "令牌和密钥"):
            connector_service.create_enterprise_connector(
                db=self.db,
                user=self.admin,
                connector_type="erp_rest",
                name="ERP",
                config={"endpoint": "https://erp.example.com/api", "api_key": "leaked"},
                credentials={"api_key": "safe"},
            )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            connector_service.create_enterprise_connector(
                db=self.db,
                user=self.admin,
                connector_type="crm_rest",
                name="CRM",
                config={"endpoint": "http://crm.example.com/api"},
                credentials={"api_key": "safe"},
            )

    def test_microsoft_oauth_uses_one_time_pkce_state_and_persists_refresh_token(self):
        connector = connector_service.create_enterprise_connector(
            db=self.db,
            user=self.admin,
            connector_type="ms_graph_onedrive",
            name="OAuth OneDrive",
            config={"tenant_id": "tenant-01", "client_id": "client-01"},
            credentials={"client_secret": "azure-client-secret"},
        )
        start = connector_service.start_microsoft_oauth(
            db=self.db,
            user=self.admin,
            connector_id=connector.id,
            redirect_uri="https://office.example.com/api/connectors/microsoft/callback",
        )
        params = parse_qs(urlparse(start["authorize_url"]).query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["client_id"], ["client-01"])
        state = params["state"][0]

        with patch(
            "app.services.connector_service.httpx.post",
            return_value=FakeTokenResponse({"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 1800}),
        ) as post:
            connected = connector_service.complete_microsoft_oauth(db=self.db, code="authorization-code", state=state)

        form = post.call_args.kwargs["data"]
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["code"], "authorization-code")
        self.assertTrue(form["code_verifier"])
        credentials = mailbox_service.decrypt_credentials(connected.credential_ciphertext)
        self.assertEqual(credentials["client_secret"], "azure-client-secret")
        self.assertEqual(credentials["access_token"], "new-access")
        self.assertEqual(credentials["refresh_token"], "new-refresh")
        rotated = connector_service.update_enterprise_credentials(
            db=self.db,
            user=self.admin,
            connector_id=connector.id,
            credentials={"client_secret": "rotated-client-secret"},
        )
        rotated_credentials = mailbox_service.decrypt_credentials(rotated.credential_ciphertext)
        self.assertEqual(rotated_credentials["client_secret"], "rotated-client-secret")
        self.assertEqual(rotated_credentials["access_token"], "new-access")
        self.assertEqual(rotated_credentials["refresh_token"], "new-refresh")
        with self.assertRaisesRegex(ValueError, "已失效"):
            connector_service.complete_microsoft_oauth(db=self.db, code="replayed", state=state)

    def test_graph_sync_refreshes_expiring_access_token(self):
        connector = connector_service.create_enterprise_connector(
            db=self.db,
            user=self.admin,
            connector_type="ms_graph_onedrive",
            name="Refresh OneDrive",
            config={"tenant_id": "tenant-01", "client_id": "client-01"},
            credentials={"client_secret": "azure-client-secret", "access_token": "expired-token", "refresh_token": "refresh-token"},
        )
        credentials = mailbox_service.decrypt_credentials(connector.credential_ciphertext)
        credentials["expires_at"] = "2020-01-01T00:00:00+00:00"
        connector.credential_ciphertext = mailbox_service.encrypt_credentials(credentials)
        self.db.commit()
        with patch(
            "app.services.enterprise_connector_service.httpx.post",
            return_value=FakeTokenResponse({"access_token": "refreshed-token", "refresh_token": "refresh-next", "expires_in": 3600}),
        ) as post:
            with patch("app.services.enterprise_connector_service.httpx.Client", FakeGraphClient):
                batch = enterprise_connector_service.build_batch(connector)

        self.assertEqual(batch.documents[0]["title"], "销售制度.docx")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        refreshed = mailbox_service.decrypt_credentials(connector.credential_ciphertext)
        self.assertEqual(refreshed["access_token"], "refreshed-token")
        self.assertEqual(refreshed["refresh_token"], "refresh-next")


if __name__ == "__main__":
    unittest.main()
