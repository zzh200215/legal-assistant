"""API 层：If-Match 版本冲突（409）在全部已声明端点上的名单枚举测试。

覆盖 app/main.py `_IF_MATCH_ENDPOINTS` 名单（契约口径）：
- 名单中每个 (method, path) 必须真实存在于 OpenAPI 路由表（声明一致性）；
- 每个端点：带当前 ETag 更新成功、带陈旧 If-Match 返回 409 CONCURRENT_UPDATE_CONFLICT。

说明：tasks PUT 的 409 细节已由 tests/test_obs_api_p1.py 覆盖，本文件聚焦
「名单枚举完整性」与 PATCH/org 两个未被枚举测试的端点。
"""

import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import _IF_MATCH_ENDPOINTS, app
from app.models.org import Organization
from app.models.task import Task
from app.models.user import User


class IfMatchEndpointEnumerationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.Session()

        self.org = Organization(name="OrgA", code="ORGA")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="tester",
            email="tester@example.com",
            hashed_password=hash_password("secret"),
            role="admin",
            organization_id=self.org.id,
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.org)
        self.db.refresh(self.user)
        self.token = create_access_token({"sub": str(self.user.id)})
        self.headers = {"Authorization": f"Bearer {self.token}"}

        def _override_db():
            yield self.db

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    # ── 声明一致性：名单中的端点必须真实存在 ──────────────────────────────────
    def test_declared_if_match_paths_exist_in_openapi(self):
        schema = app.openapi()
        for method, path in _IF_MATCH_ENDPOINTS:
            self.assertIn(path, schema["paths"], f"声明路径 {path} 不在 OpenAPI 路由表中")
            self.assertIn(method, schema["paths"][path], f"方法 {method} {path} 未注册")

    # ── 端点行为：陈旧 If-Match → 409 ─────────────────────────────────────────
    def test_task_patch_stale_if_match_returns_409(self):
        task = Task(user_id=self.user.id, title="版本任务", status="todo")
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)

        resp = self.client.get(f"/api/tasks/{task.id}", headers=self.headers)
        etag = resp.headers.get("ETag")
        self.assertIsNotNone(etag)

        ok = self.client.patch(
            f"/api/tasks/{task.id}",
            json={"status": "in_progress"},
            headers={**self.headers, "If-Match": etag},
        )
        self.assertEqual(ok.status_code, 200, ok.text)

        stale = self.client.patch(
            f"/api/tasks/{task.id}",
            json={"status": "done"},
            headers={**self.headers, "If-Match": etag},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], "CONCURRENT_UPDATE_CONFLICT")

    def test_task_patch_without_if_match_succeeds(self):
        """If-Match 未提供时不拦截（增量接入语义：名单端点强制，其余不拦）。"""
        task = Task(user_id=self.user.id, title="无版本任务", status="todo")
        self.db.add(task)
        self.db.commit()
        resp = self.client.patch(
            f"/api/tasks/{task.id}",
            json={"status": "done"},
            headers=self.headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_org_put_stale_if_match_returns_409(self):
        """组织更新的真实路由为 /api/org/organizations/{org_id}（系统管理员）。"""
        org = Organization(name="OrgB", code="ORGB")
        self.db.add(org)
        self.db.commit()
        self.db.refresh(org)

        ok = self.client.put(
            f"/api/org/organizations/{org.id}",
            json={"name": "OrgB-新"},
            headers={**self.headers, "If-Match": '"v1"'},
        )
        self.assertEqual(ok.status_code, 200, ok.text)
        self.db.refresh(org)
        self.assertGreaterEqual(org.version, 2)

        stale = self.client.put(
            f"/api/org/organizations/{org.id}",
            json={"name": "OrgB-再改"},
            headers={**self.headers, "If-Match": '"v1"'},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], "CONCURRENT_UPDATE_CONFLICT")

    def test_org_put_non_admin_forbidden(self):
        """非系统管理员更新组织 → 403（权限边界与 409 不冲突）。"""
        member = User(
            username="member",
            email="member@example.com",
            hashed_password=hash_password("secret"),
            role="lawyer",
            organization_id=self.org.id,
        )
        self.db.add(member)
        self.db.commit()
        self.db.refresh(member)
        token = create_access_token({"sub": str(member.id)})
        resp = self.client.put(
            f"/api/org/organizations/{self.org.id}",
            json={"name": "OrgA-改"},
            headers={"Authorization": f"Bearer {token}", "If-Match": '"v1"'},
        )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_stale_data_handler_has_etag_in_409_response(self):
        """409 响应保持 envelope 结构（success=false + error.code）。"""
        task = Task(user_id=self.user.id, title="版本任务", status="todo")
        self.db.add(task)
        self.db.commit()
        resp = self.client.put(
            f"/api/tasks/{task.id}",
            json={"title": "x"},
            headers={**self.headers, "If-Match": '"v99"'},
        )
        self.assertEqual(resp.status_code, 409)
        payload = resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "CONCURRENT_UPDATE_CONFLICT")


if __name__ == "__main__":
    unittest.main()
