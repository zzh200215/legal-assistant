"""CI 探针：复制 test_connector_contract 的 setUp 与流程，失败时打印 500 响应体。"""

import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User


class ConnectorProbeTests(unittest.TestCase):
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
        self.user = User(username="tester", email="tester@example.com", hashed_password=hash_password("secret"))
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        self.sessionlocal_patchers = [
            patch("app.services.llm_governance_service.SessionLocal", self.TestingSessionLocal),
            patch("app.services.llm_observability_service.SessionLocal", self.TestingSessionLocal),
            patch("app.core.database.SessionLocal", self.TestingSessionLocal),
        ]
        for patcher in self.sessionlocal_patchers:
            patcher.start()

        def override_get_db():
            db = self.TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.token = create_access_token({"sub": self.user.id})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        app.dependency_overrides.clear()
        for patcher in reversed(getattr(self, "sessionlocal_patchers", [])):
            patcher.stop()

    def test_probe_connector_contract(self):
        response = self.client.post(
            "/api/connectors/",
            headers=self.headers,
            json={"connector_type": "drive", "name": "Shared Drive", "config_json": '{"path":"contracts"}'},
        )
        self.assertEqual(response.status_code, 200, msg=f"PROBE-BODY: {response.status_code} {json.dumps(response.json(), ensure_ascii=False)[:800]}")
        connector_id = response.json()["data"]["id"]

        fake_task = type("Task", (), {"id": "connector-task-1"})()
        with patch("app.tasks.connector_sync_task.delay", return_value=fake_task):
            sync_response = self.client.post(
                f"/api/connectors/{connector_id}/sync",
                headers=self.headers,
                json={"sync_mode": "manual"},
            )
            self.assertEqual(sync_response.status_code, 200, msg=f"PROBE-SYNC: {sync_response.status_code} {json.dumps(sync_response.json(), ensure_ascii=False)[:500]}")


if __name__ == "__main__":
    unittest.main()
