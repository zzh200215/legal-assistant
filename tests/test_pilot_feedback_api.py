"""#72/退出问卷与 NPS 回收 API 回归测试"""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password, create_access_token
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class PilotFeedbackApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        org = Organization(name="PilotOrg", code="PILOT1")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id

        self.user = User(
            username="lawyer1",
            email="lawyer1@t.com",
            hashed_password=hash_password("pw"),
            role="user",
            status=UserStatus.active.value,
            organization_id=org.id,
        )
        self.db.add(self.user)
        self.db.flush()
        self.db.add(OrganizationMember(organization_id=org.id, user_id=self.user.id, legal_role="reviewer"))

        self.admin = User(
            username="admin1",
            email="admin1@t.com",
            hashed_password=hash_password("pw"),
            role="admin",
            status=UserStatus.active.value,
        )
        self.db.add(self.admin)
        self.db.commit()

        self.user_token = create_access_token({"sub": str(self.user.id)})
        self.admin_token = create_access_token({"sub": str(self.admin.id)})

        def _override_db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _override_db
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        self.db.close()
        self.engine.dispose()

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    def test_nps_submit_and_stats(self):
        r = self.client.post("/api/pilot/nps", json={"score": 9}, headers=self._headers(self.user_token))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["success"])
        self.client.post("/api/pilot/nps", json={"score": 9}, headers=self._headers(self.user_token))
        self.client.post("/api/pilot/nps", json={"score": 4}, headers=self._headers(self.user_token))

        r = self.client.get("/api/pilot/admin/nps-stats", headers=self._headers(self.admin_token))
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["promoters"], 2)
        self.assertEqual(data["detractors"], 1)
        self.assertAlmostEqual(data["nps"], 33.3, places=1)

    def test_nps_invalid_score(self):
        r = self.client.post("/api/pilot/nps", json={"score": 11}, headers=self._headers(self.user_token))
        self.assertEqual(r.status_code, 422, r.text)

    def test_exit_survey_submit_and_list(self):
        r = self.client.post(
            "/api/pilot/exit-survey",
            json={
                "nps_score": 8,
                "trust_confidence": "credible",
                "trust_citations": "frequent",
                "trust_next_steps": "clear",
                "value_ranking": "review>consult>draft",
                "review_wish": "退回原因说明",
                "pain_point": "上传慢",
                "pay_intent": "renew",
                "feature_requests": "合同对比",
                "summary_feedback": "整体满意",
            },
            headers=self._headers(self.user_token),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["success"])

        r = self.client.get("/api/pilot/admin/surveys", headers=self._headers(self.admin_token))
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["pay_intent"], "renew")
        self.assertEqual(items[0]["user_id"], self.user.id)

    def test_exit_survey_enum_validation(self):
        r = self.client.post(
            "/api/pilot/exit-survey",
            json={"pay_intent": "maybe_later"},
            headers=self._headers(self.user_token),
        )
        self.assertEqual(r.status_code, 422, r.text)

    def test_admin_requires_admin(self):
        r = self.client.get("/api/pilot/admin/nps-stats", headers=self._headers(self.user_token))
        self.assertEqual(r.status_code, 403, r.text)


if __name__ == "__main__":
    unittest.main()
