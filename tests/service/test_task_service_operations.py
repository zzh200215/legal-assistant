"""Service 层：task_service 操作与边界补测（动作项创建/拆解/同步列表/邮件要点/权限 scope）。

覆盖 app/services/jobs/task_service.py：
- create_from_action_items：字段拼接（负责人/截止/原文依据/置信度）与 due_date 解析；
- extract_from_document / extract_from_chat：LLM 提取 → 批量创建；空结果短路；
- decompose：子任务创建 + 父任务 todo→in_progress；任务不存在抛 ValueError；
- get_sub_tasks：父子关系与用户隔离；
- list_for_sync：状态过滤 / task_ids 白名单 / overdue 判定（naive+aware）/ 稳定排序；
- build_sync_email_points：要点拼接口径；
- _match_scope：mine/org/dept/all 权限 scope 判定（纯逻辑）。
"""

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.org import Organization
from app.models.user import User
from app.services.jobs.task_service import task_service


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class TaskServiceOperationsTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="TaskOrg", code="TSK")
        self.db.add(self.org)
        self.db.commit()
        self.user = User(username="tso", email="tso@example.com", hashed_password="h",
                         role="user", organization_id=self.org.id)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    # ── create_from_action_items ────────────────────────────────────────────
    def test_create_from_action_items_joins_fields(self):
        items = [{
            "task": "审核合同", "assignee": "李律师", "deadline": "2026-08-20",
            "source_text": "会议记录原文", "confidence": 0.9, "priority": "high",
        }]
        tasks = task_service.create_from_action_items(items, self.user.id, source_id=7, db=self.db)
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task.assignee, "李律师")
        self.assertIsNotNone(task.due_date)
        self.assertEqual(task.due_date.date().isoformat(), "2026-08-20")
        self.assertIn("负责人：李律师", task.description)
        self.assertIn("截止时间：2026-08-20", task.description)
        self.assertIn("原文依据：会议记录原文", task.description)
        self.assertIn("识别置信度：0.9", task.description)
        self.assertEqual(task.source_type, "meeting")
        self.assertEqual(task.source_id, 7)

    def test_create_from_action_items_empty(self):
        self.assertEqual(task_service.create_from_action_items([], self.user.id, None, db=self.db), [])

    # ── extract_from_document / chat ────────────────────────────────────────
    def test_extract_from_document(self):
        with (
            patch("app.services.documents.document_service.document_service._get_document_text", return_value="文本"),
            patch("app.services.documents.analysis_service.analysis_service.extract_document_todos",
                  AsyncMock(return_value=[{"task": "跟进付款", "deadline": "2026-08-01"}])),
        ):
            tasks = asyncio.run(task_service.extract_from_document(1, self.user.id, self.db))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "跟进付款")
        self.assertEqual(tasks[0].source_type, "document")

    def test_extract_from_document_no_todos(self):
        with (
            patch("app.services.documents.document_service.document_service._get_document_text", return_value=""),
            patch("app.services.documents.analysis_service.analysis_service.extract_document_todos",
                  AsyncMock(return_value=[])),
        ):
            tasks = asyncio.run(task_service.extract_from_document(1, self.user.id, self.db))
        self.assertEqual(tasks, [])

    def test_extract_from_chat(self):
        with patch("app.services.documents.analysis_service.analysis_service.extract_tasks_from_chat",
                   AsyncMock(return_value=[{"task": "回复客户"}])):
            tasks = asyncio.run(task_service.extract_from_chat("帮我安排回复客户", self.user.id, self.db))
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].source_type, "chat")

    # ── decompose ───────────────────────────────────────────────────────────
    def test_decompose_creates_subtasks_and_advances_parent(self):
        parent = task_service.create("大目标", self.user.id, self.db)
        with patch("app.services.documents.analysis_service.analysis_service.decompose_task",
                   AsyncMock(return_value=[
                       {"title": "子任务A", "priority": "high"},
                       {"title": "子任务B", "description": "细节"},
                   ])):
            subs = asyncio.run(task_service.decompose(parent.id, self.user.id, self.db))
        self.assertEqual(len(subs), 2)
        self.assertEqual(subs[0].parent_id, parent.id)
        self.assertEqual(subs[0].source_type, "decompose")
        self.db.refresh(parent)
        self.assertEqual(parent.status, "in_progress")  # todo → in_progress

    def test_decompose_no_items_keeps_status(self):
        parent = task_service.create("大目标2", self.user.id, self.db)
        with patch("app.services.documents.analysis_service.analysis_service.decompose_task",
                   AsyncMock(return_value=[])):
            subs = asyncio.run(task_service.decompose(parent.id, self.user.id, self.db))
        self.assertEqual(subs, [])
        self.db.refresh(parent)
        self.assertEqual(parent.status, "todo")

    def test_decompose_missing_task_raises(self):
        with self.assertRaises(ValueError):
            asyncio.run(task_service.decompose(9999, self.user.id, self.db))

    # ── get_sub_tasks ───────────────────────────────────────────────────────
    def test_get_sub_tasks_filters_by_parent_and_user(self):
        parent = task_service.create("父任务", self.user.id, self.db)
        sub = task_service.create("子任务", self.user.id, self.db, parent_id=parent.id)
        other = task_service.create("别人任务", self.user.id, self.db, parent_id=parent.id)
        self.assertEqual({t.id for t in task_service.get_sub_tasks(parent.id, self.db)}, {sub.id, other.id})
        # user 过滤
        self.assertEqual(task_service.get_sub_tasks(parent.id, self.db, user_id=999), [])

    # ── list_for_sync ───────────────────────────────────────────────────────
    def test_list_for_sync_filters_status_and_task_ids(self):
        done = task_service.create("已完成", self.user.id, self.db)
        done.status = "done"
        self.db.commit()
        todo = task_service.create("待办", self.user.id, self.db)
        in_progress = task_service.create("进行中", self.user.id, self.db)
        in_progress.status = "in_progress"
        self.db.commit()
        tasks = task_service.list_for_sync(self.user.id, self.db, role="user",
                                           organization_id=self.org.id, scope="mine")
        ids = {t.id for t in tasks}
        self.assertIn(todo.id, ids)
        self.assertIn(in_progress.id, ids)
        self.assertNotIn(done.id, ids)  # done 不进同步列表
        # task_ids 白名单
        filtered = task_service.list_for_sync(self.user.id, self.db, role="user",
                                              organization_id=self.org.id, scope="mine",
                                              task_ids=[todo.id])
        self.assertEqual([t.id for t in filtered], [todo.id])

    def test_list_for_sync_overdue_only(self):
        overdue = task_service.create("逾期任务", self.user.id, self.db,
                                      due_date=datetime.now(UTC) - timedelta(days=1))
        future = task_service.create("未来任务", self.user.id, self.db,
                                     due_date=datetime.now(UTC) + timedelta(days=1))
        none = task_service.create("无期限任务", self.user.id, self.db)
        tasks = task_service.list_for_sync(self.user.id, self.db, role="user",
                                           organization_id=self.org.id, scope="mine",
                                           include_overdue_only=True)
        ids = {t.id for t in tasks}
        self.assertIn(overdue.id, ids)
        self.assertNotIn(future.id, ids)
        self.assertNotIn(none.id, ids)

    def test_list_for_sync_sort_order(self):
        low = task_service.create("低优先", self.user.id, self.db, priority="low")
        high = task_service.create("高优先", self.user.id, self.db, priority="high")
        tasks = task_service.list_for_sync(self.user.id, self.db, role="user",
                                           organization_id=self.org.id, scope="mine")
        self.assertEqual([t.id for t in tasks], [high.id, low.id])  # 高优先在前

    # ── build_sync_email_points ─────────────────────────────────────────────
    def test_build_sync_email_points(self):
        task = task_service.create("审查合同", self.user.id, self.db, assignee="李律师",
                                   priority="high",
                                   due_date=datetime(2026, 8, 20, 9, 0, 0))
        points = task_service.build_sync_email_points([task])
        self.assertEqual(len(points), 1)
        point = points[0]
        self.assertIn("审查合同", point)
        self.assertIn("状态：待办", point)
        self.assertIn("负责人：李律师", point)
        self.assertIn("截止：2026-08-20", point)
        self.assertIn("优先级：高", point)

    # ── _match_scope（权限 scope 纯逻辑）────────────────────────────────────
    def test_match_scope_rules(self):
        m = task_service._match_scope
        # mine：仅本人
        self.assertTrue(m(1, 10, 20, user_id=1, organization_id=10, department_id=20, scope="mine"))
        self.assertFalse(m(2, 10, 20, user_id=1, organization_id=10, department_id=20, scope="mine"))
        # department：同部门
        self.assertTrue(m(2, 99, 20, user_id=1, organization_id=10, department_id=20, scope="department"))
        self.assertFalse(m(2, 99, 21, user_id=1, organization_id=10, department_id=20, scope="department"))
        # organization：同组织（且非本人/非同部门）
        self.assertTrue(m(2, 10, 21, user_id=1, organization_id=10, department_id=20, scope="organization"))
        self.assertFalse(m(2, 99, 21, user_id=1, organization_id=10, department_id=20, scope="organization"))
        # shared：同部门或同组织
        self.assertTrue(m(2, 10, 21, user_id=1, organization_id=10, department_id=20, scope="shared"))
        self.assertTrue(m(2, 99, 20, user_id=1, organization_id=10, department_id=20, scope="shared"))
        # all / 未知 scope：可见
        self.assertTrue(m(2, 99, 21, user_id=1, organization_id=10, department_id=20, scope="all"))
        self.assertTrue(m(2, 99, 21, user_id=1, organization_id=10, department_id=20, scope="unknown"))


if __name__ == "__main__":
    unittest.main()
