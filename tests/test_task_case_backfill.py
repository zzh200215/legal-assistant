"""历史任务 case_id 回填：来源链路、dry-run、幂等与边界。"""

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.document import Document
from app.models.legal import LegalCase, LegalConsultation
from app.models.org import Organization
from app.models.task import Task
from app.models.user import User
from app.services.jobs.task_case_backfill import backfill_task_case_ids


class TaskCaseBackfillTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        org = Organization(name="回填组织", code="backfill_org")
        self.db.add(org)
        self.db.flush()
        user = User(
            username="backfill",
            email="backfill@example.com",
            hashed_password="h",
            organization_id=org.id,
        )
        self.db.add(user)
        self.db.flush()
        self.case = LegalCase(
            title="回填案件", case_type="other", organization_id=org.id, user_id=user.id
        )
        self.other_case = LegalCase(
            title="另一案件", case_type="other", organization_id=org.id, user_id=user.id
        )
        self.db.add_all([self.case, self.other_case])
        self.db.flush()
        self.user_id = user.id

    def tearDown(self):
        self.db.close()
        self.db.get_bind().dispose()

    def _consult(self, case_id):
        row = LegalConsultation(
            user_id=self.user_id,
            case_id=case_id,
            question="问",
            category="other",
            known_facts_json="[]",
            missing_facts_json="[]",
            references_json="[]",
            advice="答",
            risk_level="low",
            status="draft",
        )
        self.db.add(row)
        self.db.flush()
        return row

    def _doc(self, metadata):
        doc = Document(
            user_id=self.user_id,
            title="资料",
            file_type="txt",
            status="ready",
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
        )
        self.db.add(doc)
        self.db.flush()
        return doc

    def _task(self, title, **kwargs):
        task = Task(user_id=self.user_id, title=title, **kwargs)
        self.db.add(task)
        self.db.flush()
        return task

    def _seed_chain(self):
        consult = self._consult(self.case.id)
        doc_int = self._doc({"case_id": self.case.id})
        doc_str = self._doc({"case_id": str(self.other_case.id)})
        t_consult = self._task("咨询跟进", source_type="legal_consultation", source_id=consult.id)
        t_doc = self._task("文档待办", source_type="document", source_id=doc_int.id)
        t_doc_str = self._task("文档待办(字符串)", source_type="document", source_id=doc_str.id)
        t_sub = self._task("拆解一级", source_type="decompose", source_id=t_doc.id, parent_id=t_doc.id)
        t_grand = self._task("拆解二级", source_type="decompose", source_id=t_sub.id, parent_id=t_sub.id)
        t_existing = self._task("已有案件", case_id=self.other_case.id)
        self.db.commit()
        return {
            "consult": t_consult,
            "doc": t_doc,
            "doc_str": t_doc_str,
            "sub": t_sub,
            "grand": t_grand,
            "existing": t_existing,
        }

    def test_dry_run_plans_without_writing(self):
        seeded = self._seed_chain()

        report = backfill_task_case_ids(self.db, apply=False)

        self.assertEqual(report.total_without_case, 5)
        self.assertEqual(report.scanned, 5)
        self.assertEqual(report.planned, 5)
        self.assertEqual(report.applied, 0)
        self.assertEqual(
            report.by_rule, {"consultation": 1, "document": 2, "parent": 2}
        )
        self.assertEqual(len(report.samples), 5)
        self.db.expire_all()
        self.assertIsNone(self.db.get(Task, seeded["consult"].id).case_id)
        self.assertIsNone(self.db.get(Task, seeded["grand"].id).case_id)
        self.assertEqual(
            self.db.get(Task, seeded["existing"].id).case_id, self.other_case.id
        )

    def test_apply_sets_case_id_and_is_idempotent(self):
        seeded = self._seed_chain()

        report = backfill_task_case_ids(self.db, apply=True)

        self.assertEqual(report.applied, 5)
        self.db.expire_all()
        self.assertEqual(
            self.db.get(Task, seeded["consult"].id).case_id, self.case.id
        )
        self.assertEqual(self.db.get(Task, seeded["doc"].id).case_id, self.case.id)
        self.assertEqual(
            self.db.get(Task, seeded["doc_str"].id).case_id, self.other_case.id
        )
        self.assertEqual(self.db.get(Task, seeded["sub"].id).case_id, self.case.id)
        self.assertEqual(self.db.get(Task, seeded["grand"].id).case_id, self.case.id)
        self.assertEqual(
            self.db.get(Task, seeded["existing"].id).case_id, self.other_case.id
        )

        again = backfill_task_case_ids(self.db, apply=True)
        self.assertEqual(again.total_without_case, 0)
        self.assertEqual(again.scanned, 0)
        self.assertEqual(again.planned, 0)

    def test_skips_unsupported_missing_and_deleted_sources(self):
        consult_none = self._consult(None)
        doc_no_case = self._doc({})
        doc_missing_case = self._doc({"case_id": 999_999})
        self._task("聊天", source_type="chat", source_id=None)
        self._task("会议", source_type="meeting", source_id=1)
        self._task("文档不存在", source_type="document", source_id=999_999)
        self._task("文档无案件", source_type="document", source_id=doc_no_case.id)
        self._task("文档案件已删", source_type="document", source_id=doc_missing_case.id)
        self._task("咨询无案件", source_type="legal_consultation", source_id=consult_none.id)
        self._task("咨询不存在", source_type="legal_consultation", source_id=888_888)
        self._task("父任务不存在", source_type="decompose", source_id=777_777, parent_id=777_777)
        self.db.commit()

        report = backfill_task_case_ids(self.db, apply=True)

        self.assertEqual(report.scanned, 8)
        self.assertEqual(report.planned, 0)
        self.assertEqual(report.applied, 0)
        self.assertEqual(report.skipped_no_source, 2)
        self.assertEqual(report.skipped_source_without_case, 2)
        self.assertEqual(report.skipped_missing_source, 3)
        self.assertEqual(report.skipped_missing_case, 1)

    def test_cycle_in_parent_chain_does_not_loop(self):
        first = self._task("环一", source_type="decompose")
        second = self._task("环二", source_type="decompose", parent_id=first.id)
        first.parent_id = second.id
        self.db.commit()

        report = backfill_task_case_ids(self.db, apply=True)

        self.assertEqual(report.planned, 0)
        self.assertEqual(report.skipped_missing_source, 2)

    def test_limit_caps_scanned_candidates(self):
        self._task("咨询一", source_type="legal_consultation", source_id=self._consult(self.case.id).id)
        self._task("咨询二", source_type="legal_consultation", source_id=self._consult(self.case.id).id)
        self._task("咨询三", source_type="legal_consultation", source_id=self._consult(self.case.id).id)
        self.db.commit()

        report = backfill_task_case_ids(self.db, apply=True, limit=2)

        self.assertEqual(report.total_without_case, 3)
        self.assertEqual(report.scanned, 2)
        self.assertEqual(report.planned, 2)
        self.assertEqual(report.applied, 2)

        rest = backfill_task_case_ids(self.db, apply=True)
        self.assertEqual(rest.total_without_case, 1)
        self.assertEqual(rest.planned, 1)
