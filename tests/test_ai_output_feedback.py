"""#73/端侧 AI 输出 👍/👎 埋点回归测试（feedback_score 列式方案，三端点）"""
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
from app.models.legal import LegalConsultation, ContractReview, LegalDraft


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class AiOutputFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        self.user = User(
            username="owner1", email="o1@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value,
        )
        self.other = User(
            username="other1", email="x1@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value,
        )
        self.db.add_all([self.user, self.other])
        self.db.flush()

        self.consultation = LegalConsultation(
            user_id=self.user.id, question="被辞退怎么维权", category="labor",
            advice="建议协商", risk_level="medium", status="completed",
        )
        self.review = ContractReview(
            user_id=self.user.id, title="服务合同", content="正文", version=1,
            status="completed", summary="存在风险", risks_json="[]", references_json="[]",
        )
        self.draft = LegalDraft(
            user_id=self.user.id, title="申请书", document_type="labor_arbitration_application",
            content="正文", status="completed",
        )
        self.db.add_all([self.consultation, self.review, self.draft])
        self.db.commit()

        self.token = create_access_token({"sub": str(self.user.id)})
        self.other_token = create_access_token({"sub": str(self.other.id)})

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

    def test_consultation_like_writes_score(self):
        r = self.client.post(
            f"/api/legal/consultations/{self.consultation.id}/feedback",
            json={"score": 1},
            headers=self._headers(self.token),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["feedback_score"], 1)
        self.db.refresh(self.consultation)
        self.assertEqual(self.consultation.feedback_score, 1)

    def test_review_dislike_with_note(self):
        r = self.client.post(
            f"/api/legal/contract-reviews/{self.review.id}/feedback",
            json={"score": -1, "note": "引用失效"},
            headers=self._headers(self.token),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.db.refresh(self.review)
        self.assertEqual(self.review.feedback_score, -1)
        self.assertEqual(self.review.feedback_note, "引用失效")

    def test_draft_feedback(self):
        r = self.client.post(
            f"/api/legal/drafts/{self.draft.id}/feedback",
            json={"score": 1},
            headers=self._headers(self.token),
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.db.refresh(self.draft)
        self.assertEqual(self.draft.feedback_score, 1)

    def test_invalid_score_rejected(self):
        r = self.client.post(
            f"/api/legal/consultations/{self.consultation.id}/feedback",
            json={"score": 2},
            headers=self._headers(self.token),
        )
        self.assertEqual(r.status_code, 400, r.text)

    def test_non_owner_cannot_feedback(self):
        r = self.client.post(
            f"/api/legal/consultations/{self.consultation.id}/feedback",
            json={"score": -1},
            headers=self._headers(self.other_token),
        )
        self.assertEqual(r.status_code, 404, r.text)

    def test_unauthorized_rejected(self):
        r = self.client.post(
            f"/api/legal/consultations/{self.consultation.id}/feedback",
            json={"score": 1},
        )
        self.assertEqual(r.status_code, 401, r.text)


if __name__ == "__main__":
    unittest.main()
