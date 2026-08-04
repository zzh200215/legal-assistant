"""P-5 留存与北极星：/api/admin/retention 按注册周分群 D7/D30 留存；/api/admin/north-star 周活跃律师"""
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import create_access_token, hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.legal import LegalCase, LegalConsultation, ContractReview, LegalDraft
from app.api.dashboard_api import _week_start


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DashboardRetentionNorthStarTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()
        self._seed()

        app.dependency_overrides[get_db] = lambda: self.db
        token = create_access_token({"sub": str(self.admin.id)})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"Authorization": f"Bearer {token}"}

    def _seed(self):
        now = _utcnow()

        def _user(name, email, role="user", created_at=None):
            u = User(
                username=name, email=email,
                hashed_password=hash_password("pw"), role=role,
                status=UserStatus.active.value,
                created_at=created_at or now,
            )
            self.db.add(u)
            return u

        self.admin = _user("admin", "a@t.com", role="admin")
        # 40 天前注册的用户 A：D7 窗口（reg+7~14）与 D30 窗口（reg+28~35）各回归一次
        self.reg_a = now - timedelta(days=40)
        self.uA = _user("uA", "uA@t.com", created_at=self.reg_a)
        # 用户 B：同期注册但无任何任务（留存分母应计入）
        self.uB = _user("uB", "uB@t.com", created_at=self.reg_a)
        # 用户 C：5 天前注册，D7 窗口尚未完全经历
        self.uC = _user("uC", "uC@t.com", created_at=now - timedelta(days=5))
        self.db.commit()
        self.db.refresh(self.admin)
        self.db.refresh(self.uA)
        self.db.refresh(self.uB)
        self.db.refresh(self.uC)

        c = LegalConsultation(user_id=self.uA.id, category="labor", question="q", advice="a")
        c.created_at = self.reg_a + timedelta(days=8)
        self.db.add(c)
        d = LegalDraft(user_id=self.uA.id, document_type="labor_arbitration_application", title="申请书")
        d.created_at = self.reg_a + timedelta(days=30)
        self.db.add(d)

        # admin 排除：有任务也不计入队列
        c_adm = LegalConsultation(user_id=self.admin.id, category="labor", question="q", advice="a")
        c_adm.created_at = now - timedelta(days=1)
        self.db.add(c_adm)
        self.db.commit()
        self.db.refresh(self.admin)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.db.close()

    def _get_retention(self, days=90):
        resp = self.client.get(f"/api/admin/retention?days={days}", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        return resp.json()["data"]

    def _get_north_star(self, weeks=4):
        resp = self.client.get(f"/api/admin/north-star?weeks={weeks}", headers=self.headers)
        self.assertEqual(resp.status_code, 200)
        return resp.json()["data"]

    # ---------- retention ----------

    def test_retention_d7_d30_rate(self):
        data = self._get_retention()
        # A、B 同一注册周（40 天前）；C 单独一周（5 天前，窗口未观察）
        cohorts = {c["week_start"]: c for c in data["cohorts"]}
        self.assertEqual(len(cohorts), 2)
        a_week = cohorts[data["cohorts"][0]["week_start"]]
        # 老群：D7/D30 均 fully observed
        self.assertEqual(a_week["cohort_size"], 2)
        self.assertEqual(a_week["d7"]["observed"], 2)
        self.assertEqual(a_week["d7"]["active"], 1)
        self.assertEqual(a_week["d7"]["rate"], 0.5)
        self.assertEqual(a_week["d30"]["observed"], 2)
        self.assertEqual(a_week["d30"]["active"], 1)
        self.assertEqual(a_week["d30"]["rate"], 0.5)

    def test_retention_unobserved_window_is_none(self):
        data = self._get_retention()
        c = data["cohorts"][-1]  # C 的注册周（最近一周）
        self.assertEqual(c["cohort_size"], 1)
        self.assertEqual(c["d7"]["observed"], 0)
        self.assertIsNone(c["d7"]["rate"])
        self.assertIsNone(c["d30"]["rate"])

    def test_retention_summary_pooled(self):
        data = self._get_retention()
        self.assertEqual(data["summary"]["d7"]["observed"], 2)
        self.assertEqual(data["summary"]["d7"]["rate"], 0.5)
        self.assertEqual(data["summary"]["d30"]["observed"], 2)
        self.assertEqual(data["summary"]["d30"]["rate"], 0.5)

    def test_retention_excludes_admin(self):
        data = self._get_retention()
        total_size = sum(c["cohort_size"] for c in data["cohorts"])
        self.assertEqual(total_size, 3)  # A/B/C，admin 不计

    def test_retention_empty_database(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db = Session()
        admin = User(
            username="admin", email="a@t.com",
            hashed_password=hash_password("pw"), role="admin",
            status=UserStatus.active.value, created_at=_utcnow(),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app, raise_server_exceptions=False)
        try:
            resp = client.get("/api/admin/retention", headers={"Authorization": f"Bearer {create_access_token({'sub': str(admin.id)})}"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()["data"]
            self.assertEqual(data["cohorts"], [])
            self.assertIsNone(data["summary"]["d7"]["rate"])
            self.assertIsNone(data["summary"]["d30"]["rate"])
        finally:
            app.dependency_overrides.clear()
            db.close()

    # ---------- north-star ----------

    def test_north_star_weekly_metrics(self):
        now = _utcnow()
        # u_active：本周活跃 + 当前持有进行中案件 → with_active_case 计入
        u_active = User(
            username="u_active", email="ua@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, created_at=now,
        )
        self.db.add(u_active)
        # u_case_task：本周审查挂在已关闭案件下 → 计 case_tasks，但不算进行中案件
        u_case = User(
            username="u_case", email="uc@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, created_at=now,
        )
        self.db.add(u_case)
        # u_last：上周唯一活跃
        u_last = User(
            username="u_last", email="ul@t.com",
            hashed_password=hash_password("pw"), role="user",
            status=UserStatus.active.value, created_at=now - timedelta(days=15),
        )
        self.db.add(u_last)
        self.db.commit()
        self.db.refresh(u_active)
        self.db.refresh(u_case)
        self.db.refresh(u_last)

        case_open = LegalCase(
            organization_id=1, user_id=u_active.id, title="进行中案件",
            case_type="labor_dispute", status="in_progress",
        )
        self.db.add(case_open)
        case_closed = LegalCase(
            organization_id=1, user_id=u_case.id, title="已关闭案件",
            case_type="contract_dispute", status="closed",
        )
        self.db.add(case_closed)
        self.db.commit()
        self.db.refresh(case_closed)

        c_now = LegalConsultation(user_id=u_active.id, category="labor", question="q", advice="a")
        c_now.created_at = now
        self.db.add(c_now)
        r = ContractReview(user_id=u_case.id, case_id=case_closed.id, title="c", content="合同")
        r.created_at = now
        self.db.add(r)
        d_last = LegalDraft(user_id=u_last.id, document_type="consumer_complaint", title="投诉书")
        # 本周一前一天 = 上一周周日的时刻，确定落在上一周（周一为周首）桶
        d_last.created_at = _week_start(now) - timedelta(days=1)
        self.db.add(d_last)
        self.db.commit()

        data = self._get_north_star(weeks=4)
        self.assertEqual(len(data["weekly"]), 4)
        cur = data["weekly"][-1]                      # 本周
        prev = data["weekly"][-2]                     # 上周
        self.assertEqual(cur["active_lawyers"], 2)    # u_active + u_case
        self.assertEqual(cur["with_active_case"], 1)  # 仅 u_active
        self.assertEqual(cur["tasks"], 2)
        self.assertEqual(cur["case_tasks"], 1)        # 仅挂 case_id 的审查
        self.assertEqual(prev["active_lawyers"], 1)   # 仅 u_last
        self.assertEqual(prev["tasks"], 1)
        # 周环比：active_lawyers (2-1)/1 = 100%；case_tasks 上周为 0 → None
        pct = data["weekly_change_pct"]
        self.assertEqual(pct["active_lawyers"], 100.0)
        self.assertEqual(pct["tasks"], 100.0)
        self.assertIsNone(pct["case_tasks"])
        self.assertIsNone(pct["with_active_case"])
        self.assertEqual(data["current"]["week_start"], cur["week_start"])

    def test_north_star_empty_database(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        db = Session()
        admin = User(
            username="admin", email="a@t.com",
            hashed_password=hash_password("pw"), role="admin",
            status=UserStatus.active.value, created_at=_utcnow(),
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app, raise_server_exceptions=False)
        try:
            resp = client.get("/api/admin/north-star", headers={"Authorization": f"Bearer {create_access_token({'sub': str(admin.id)})}"})
            self.assertEqual(resp.status_code, 200)
            data = resp.json()["data"]
            self.assertEqual(len(data["weekly"]), 12)  # 默认 12 周
            self.assertEqual(data["current"]["active_lawyers"], 0)
            self.assertEqual(data["current"]["tasks"], 0)
        finally:
            app.dependency_overrides.clear()
            db.close()

    def test_non_admin_forbidden(self):
        token = create_access_token({"sub": str(self.uA.id)})
        for path in ("/api/admin/retention", "/api/admin/north-star"):
            resp = self.client.get(path, headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 403)
            self.assertIn("ADMIN_REQUIRED", resp.text)


if __name__ == "__main__":
    unittest.main()
