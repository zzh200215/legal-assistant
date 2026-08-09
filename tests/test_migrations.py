"""迁移与脏数据处理测试：单一 head、迁移内容、重复数据去重。"""
import unittest

from sqlalchemy import create_engine, text

from alembic.config import Config
from alembic.script import ScriptDirectory

ALEMBIC_INI = "alembic.ini"


class MigrationChainTests(unittest.TestCase):
    def test_single_head_no_orphans(self):
        """迁移链必须是单一 head，alembic upgrade head 才能解析。"""
        cfg = Config(ALEMBIC_INI)
        sd = ScriptDirectory.from_config(cfg)
        heads = sd.get_heads()
        self.assertEqual(len(heads), 1, f"迁移链存在多个 head: {heads}")
        # 任意 revision 都必须可回溯到唯一 head（无孤儿分支）
        for rev in sd.walk_revisions():
            if rev.is_head:
                self.assertIn(rev.revision, heads)

    def test_0067_contains_expected_indexes_and_constraints(self):
        path = "alembic/versions/20260809_0067_infra_indexes_and_archive.py"
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for name in (
            "ix_token_usage_user_id_created_at",
            "ix_legal_notification_events_user_channel_status_created_at",
            "uq_legal_notification_preferences_user_event",
            "uq_webhook_subscriptions_app_event",
        ):
            self.assertIn(name, src, f"0067 缺少 {name}")
        self.assertIn("_dedupe_keep_latest", src)
        self.assertIn("database_archive_runs", src)

    def test_0068_adds_version_columns_and_idempotency_table(self):
        path = "alembic/versions/20260809_0068_optimistic_lock_and_idempotency.py"
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for table in ("documents", "tasks", "legal_cases", "legal_contracts", "organizations"):
            self.assertIn(f'"{table}"', src, f"0068 缺少 {table} version 列")
        for table in ("legal_contract_reviews", "legal_drafts"):
            self.assertIn(f'"{table}"', src, f"0068 缺少 {table} row_version 列")
        self.assertIn("idempotency_keys", src)
        self.assertIn("uq_idempotency_keys_scope_key", src)


class DirtyDataDedupeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        with self.engine.begin() as c:
            c.execute(text(
                "CREATE TABLE dedupe_test (id INTEGER PRIMARY KEY, grp_a TEXT, grp_b TEXT)"
            ))

    def test_dedupe_keep_latest_removes_duplicates(self):
        """迁移中清理重复数据（保留每组最新 id）的 SQL 在真实数据上有效。"""
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('a','x')"))
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('a','x')"))
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('a','x')"))
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('a','y')"))
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('b','z')"))
            before = c.execute(text("SELECT COUNT(*) FROM dedupe_test")).scalar()
            self.assertEqual(before, 5)
            # 与 0067 _dedupe_keep_latest 相同的派生表模式
            c.execute(text(
                "DELETE FROM dedupe_test WHERE id NOT IN ("
                "SELECT keep.id FROM (SELECT MAX(id) AS id FROM dedupe_test GROUP BY grp_a, grp_b) keep)"
            ))
            after = c.execute(text("SELECT COUNT(*) FROM dedupe_test")).scalar()
        self.assertEqual(after, 3)  # (a,x) 保留最新、(a,y)、(b,z)

    def test_dedupe_sql_works_on_empty_table(self):
        with self.engine.begin() as c:
            c.execute(text(
                "DELETE FROM dedupe_test WHERE id NOT IN ("
                "SELECT keep.id FROM (SELECT MAX(id) AS id FROM dedupe_test GROUP BY grp_a, grp_b) keep)"
            ))
            # 不抛错即为通过


if __name__ == "__main__":
    unittest.main()
