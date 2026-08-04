"""CI 探针：打印 connector create 500 的响应体（复现 test_connector_contract 的 CI 失败）。"""

import json
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.models.user import User
from app.services.auth_service import hash_password


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
        self.user = User(username="probe", email="probe@example.com", hashed_password=hash_password("secret"))
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.client = TestClient(app)

    def test_probe_connector_create(self):
        login = self.client.post(
            "/api/auth/login",
            json={"username": "probe", "password": "secret"},
        )
        print("PROBE login:", login.status_code, login.text[:200])
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        response = self.client.post(
            "/api/connectors/",
            headers=headers,
            json={"connector_type": "drive", "name": "Shared Drive", "config_json": '{"path":"contracts"}'},
        )
        print("PROBE create:", response.status_code, json.dumps(response.json(), ensure_ascii=False)[:500])
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
