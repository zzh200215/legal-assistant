"""Repository 层：SQLAlchemy + SQLite 内存库的 CRUD 与查询边界测试。

覆盖代表性模型（Task / Organization）：
- 分页（offset/limit）、排序（created_at + id 稳定序）、状态过滤 + count；
- 唯一约束（Organization.code/name 重复 → IntegrityError）；
- 可空字段边界（due_date/collaborators 为 NULL 的读写）；
- 乐观锁 version 列在普通更新下的自增行为（与 409 语义配套的底层保障）。

注：并发/版本冲突语义（StaleDataError → 409）已由 test_optimistic_lock 与
test_if_match_endpoints_409 覆盖，本文件只测单会话 CRUD 边界，不重复。
"""

import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.org import Organization
from app.models.task import Task, TaskStatus


class RepositoryQueryBoundaryTests(unittest.TestCase):
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
        self.org = Organization(name="OrgX", code="ORGX")
        self.db.add(self.org)
        self.db.commit()
        self.db.refresh(self.org)

    def tearDown(self):
        self.db.close()

    def _add_task(self, title, status=TaskStatus.todo.value, due_date=None):
        task = Task(
            user_id=1,
            organization_id=self.org.id,
            title=title,
            status=status,
            due_date=due_date,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def test_pagination_offset_limit(self):
        for i in range(5):
            self._add_task(f"任务-{i}")
        tasks = self.db.query(Task).order_by(Task.id).offset(1).limit(2).all()
        self.assertEqual([t.title for t in tasks], ["任务-1", "任务-2"])

    def test_order_by_created_at_then_id_is_stable(self):
        self._add_task("A")
        self._add_task("B")
        rows = self.db.query(Task).order_by(Task.created_at, Task.id).all()
        self.assertEqual([r.title for r in rows], ["A", "B"])

    def test_status_filter_with_count(self):
        self._add_task("todo-1", status=TaskStatus.todo.value)
        self._add_task("todo-2", status=TaskStatus.todo.value)
        self._add_task("done-1", status=TaskStatus.done.value)
        q = self.db.query(Task).filter(Task.status == TaskStatus.todo.value)
        self.assertEqual(q.count(), 2)
        self.assertEqual(q.first().title, "todo-1")

    def test_organization_code_unique_constraint(self):
        dup = Organization(name="OrgX2", code="ORGX")
        self.db.add(dup)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_nullable_columns_roundtrip(self):
        task = self._add_task("无期限任务", due_date=None)
        self.assertIsNone(task.due_date)
        self.assertIsNone(task.collaborators)
        task.description = None
        self.db.commit()
        self.db.refresh(task)
        self.assertIsNone(task.description)

    def test_due_date_roundtrip_preserves_value(self):
        due = datetime.now(UTC) + timedelta(days=3)
        task = self._add_task("有期限任务", due_date=due)
        self.db.refresh(task)
        # SQLite 存储 naive 值；仅验证非空与日期部分一致
        self.assertIsNotNone(task.due_date)
        self.assertEqual(task.due_date.date(), due.date())

    def test_version_increments_on_normal_update(self):
        task = self._add_task("版本递增")
        self.assertEqual(task.version, 1)
        task.title = "版本递增-改"
        self.db.commit()
        self.db.refresh(task)
        self.assertGreaterEqual(task.version, 2)


if __name__ == "__main__":
    unittest.main()
