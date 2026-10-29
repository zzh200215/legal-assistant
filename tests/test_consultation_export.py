"""咨询结果 Markdown 导出：确定性拼装，不触发模型/文件写入。"""

import json
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal import LegalConsultation, LegalConsultationExport
from app.models.org import Organization
from app.models.user import User
from app.services.legal.consultation_export import (
    build_consultation_markdown,
    prune_all_consultation_exports,
)


def _consultation(**overrides):
    row = LegalConsultation(
        user_id=1,
        case_id=7,
        question="公司未支付经济补偿金怎么办？",
        category="labor_dispute",
        known_facts_json=json.dumps(["2021年3月入职", "2024年5月被辞退"], ensure_ascii=False),
        missing_facts_json="[]",
        references_json=json.dumps([
            {
                "title": "劳动合同法", "citation": "劳动合同法第47条",
                "verification": {"verified": True},
            }
        ], ensure_ascii=False),
        advice="可以主张经济补偿。",
        risk_level="medium",
        status="pending_review",
        evidence_json=json.dumps({
            "references": 1, "article_checked": 1, "article_verified": 1,
        }, ensure_ascii=False),
        created_at=datetime(2026, 10, 28, 10, 0, 0),
        updated_at=datetime(2026, 10, 28, 10, 30, 0),
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class ConsultationExportMarkdownTests(unittest.TestCase):
    def test_includes_result_fields_and_reference_verification(self):
        content = build_consultation_markdown(_consultation())
        self.assertIn("# 法律咨询结果", content)
        self.assertIn("**咨询问题：** 公司未支付经济补偿金怎么办？", content)
        self.assertIn("**当前状态：** 待审核", content)
        self.assertIn("**风险等级：** 中", content)
        self.assertIn("**关联案件：** #7", content)
        self.assertIn("## 结论与建议", content)
        self.assertIn("可以主张经济补偿。", content)
        self.assertIn("- 2021年3月入职", content)
        self.assertIn("劳动合同法 劳动合同法第47条（已核验）", content)
        self.assertIn("## 证据可核验性", content)
        self.assertIn("AI 辅助生成", content)
        self.assertIn("<!-- content_version=", content)

    def test_empty_facts_and_references_use_explicit_empty_copy(self):
        content = build_consultation_markdown(_consultation(
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            evidence_json=None,
        ))
        self.assertIn("## 已知事实\n\n- 暂无", content)
        self.assertIn("## 待补充事实\n\n- 无缺失", content)
        self.assertIn("## 参考依据\n\n- 暂无", content)
        self.assertIn("## 证据可核验性", content)
        self.assertIn("- 引用 0 条", content)
        self.assertIn("- 等级：none", content)

    def test_status_and_risk_fall_back_to_raw_value(self):
        content = build_consultation_markdown(_consultation(status="weird_status", risk_level="unknown"))
        self.assertIn("**当前状态：** weird_status", content)
        self.assertIn("**风险等级：** unknown", content)


class PruneConsultationExportsTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.org = Organization(name="导出所", code="EXPORT")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="export_user", email="export@example.com",
            hashed_password=hash_password("pw"), organization_id=self.org.id,
        )
        self.db.add(self.user)
        self.db.flush()
        self.consultation = LegalConsultation(
            user_id=self.user.id, question="导出保留测试", category="other",
            known_facts_json="[]", missing_facts_json="[]", references_json="[]",
            advice="建议", risk_level="low", status="pending_review",
        )
        self.db.add(self.consultation)
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_prune_all_keeps_latest_and_drops_old(self):
        now = utc_now()
        for index in range(25):
            self.db.add(LegalConsultationExport(
                consultation_id=self.consultation.id, user_id=self.user.id,
                format="markdown", filename=f"export-{index}.md", content="x",
                created_at=now - timedelta(minutes=index),
            ))
        self.db.add(LegalConsultationExport(
            consultation_id=self.consultation.id, user_id=self.user.id,
            format="markdown", filename="too-old.md", content="x",
            created_at=now - timedelta(days=120),
        ))
        self.db.commit()

        result = prune_all_consultation_exports(
            self.db, now=now, retention_days=90, max_records=20,
        )
        self.db.commit()
        rows = self.db.query(LegalConsultationExport).all()
        self.assertEqual(len(rows), 20)
        self.assertEqual(result["retention_days"], 90)
        self.assertEqual(result["max_records"], 20)
        self.assertNotIn("too-old.md", [row.filename for row in rows])

    def test_beat_schedule_registers_prune_task(self):
        from app.core.celery_app import celery_app

        entry = celery_app.conf.beat_schedule["prune-legal-consultation-exports"]
        self.assertEqual(entry["task"], "prune_legal_consultation_exports")


if __name__ == "__main__":
    unittest.main()