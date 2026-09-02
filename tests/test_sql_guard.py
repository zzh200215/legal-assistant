"""SQLTool 只读安全边界：AST 级校验拒绝写/多语句/越权/危险函数/目录，白名单、脱敏，
以及语句超时下推与错误面不回传 SQL 原文。"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base
from app.mcp.sql_guard import SqlGuardError, check_read_only, redact_rows
from app.models.user import User
from app.tools.sql_tool import SQLTool, _statement_timeout_statements


class SqlReadOnlyGuardTests(unittest.TestCase):
    def test_select_and_with_select_allowed(self):
        for sql in ("SELECT a FROM t", "WITH c AS (SELECT 1) SELECT * FROM c", "SELECT * FROM pub_tbl LIMIT 5"):
            r = check_read_only(sql, allowed_tables={"pub_tbl", "t", "c"})
            self.assertTrue(r.ok, sql)
            self.assertIsNotNone(r.normalized_template)
            self.assertIsNotNone(r.param_hash)

    def test_rejects_dml_and_ddl(self):
        for sql in (
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a=1",
            "DELETE FROM t",
            "MERGE INTO t USING s ON x",
            "CREATE TABLE t (a INT)",
            "DROP TABLE t",
            "ALTER TABLE t ADD COLUMN a INT",
            "TRUNCATE TABLE t",
        ):
            r = check_read_only(sql, allowed_tables={"t"})
            self.assertFalse(r.ok, sql)
            # 部分方言无法解析（如 MERGE）→ SQL_PARSE_ERROR 同样拒绝
            self.assertIn(r.error_code, {"SQL_NOT_READ_ONLY", "SQL_PARSE_ERROR"}, sql)

    def test_rejects_multi_statement_and_comment_bypass(self):
        for sql in (
            "SELECT * FROM t; DROP TABLE t",
            "SELECT * FROM t; -- trailing",
            "SELECT 1; SELECT 2",
        ):
            r = check_read_only(sql, allowed_tables={"t"})
            self.assertFalse(r.ok, sql)
            self.assertEqual(r.error_code, "SQL_MULTI_STATEMENT", sql)

    def test_rejects_grant_show_call_command(self):
        for sql in ("GRANT ALL ON t TO u", "SHOW TABLES", "EXPLAIN SELECT 1", "CALL myproc(1)"):
            r = check_read_only(sql)
            self.assertFalse(r.ok, sql)

    def test_rejects_catalog_and_out_of_scope_tables(self):
        self.assertEqual(
            check_read_only("SELECT * FROM information_schema.tables").error_code, "SQL_CATALOG_DENIED"
        )
        self.assertEqual(
            check_read_only("SELECT * FROM pg_catalog.pg_tables").error_code, "SQL_CATALOG_DENIED"
        )
        self.assertEqual(
            check_read_only("SELECT * FROM sqlite_master").error_code, "SQL_CATALOG_DENIED"
        )
        r = check_read_only("SELECT * FROM secret_tbl", allowed_tables={"pub_tbl"})
        self.assertEqual(r.error_code, "SQL_TABLE_DENIED")

    def test_rejects_dangerous_functions(self):
        for sql in (
            'SELECT LOAD_FILE("/etc/passwd")',
            "SELECT SLEEP(5)",
            "SELECT BENCHMARK(1000000, MD5(1))",
        ):
            r = check_read_only(sql)
            self.assertFalse(r.ok, sql)
            self.assertEqual(r.error_code, "SQL_DANGEROUS_FUNCTION", sql)

    def test_template_normalizes_literals(self):
        r1 = check_read_only("SELECT a FROM t WHERE x = 1 AND y = 'v1'", allowed_tables={"t"})
        r2 = check_read_only("SELECT a FROM t WHERE x = 2 AND y = 'v2'", allowed_tables={"t"})
        self.assertTrue(r1.ok)
        self.assertNotIn("'v1'", r1.normalized_template)
        self.assertNotIn("'v2'", r2.normalized_template)
        # 同一模板不同字面量 → 模板一致、哈希一致（审计不泄露参数值）
        self.assertEqual(r1.normalized_template, r2.normalized_template)
        self.assertEqual(r1.param_hash, r2.param_hash)

    def test_empty_and_parse_error_rejected(self):
        self.assertEqual(check_read_only("").error_code, "SQL_EMPTY")
        self.assertEqual(check_read_only("   ").error_code, "SQL_EMPTY")
        self.assertEqual(check_read_only("SELECT * INTO OUTFILE '/tmp/x'").error_code, "SQL_PARSE_ERROR")

    def test_redact_rows_masks_sensitive_columns(self):
        rows = [{"email": "a@b.com", "name": "x", "Phone": "123"}]
        out = redact_rows(rows, {"email", "phone"})
        self.assertEqual(out[0]["email"], "****")
        self.assertEqual(out[0]["Phone"], "****")
        self.assertEqual(out[0]["name"], "x")

    def test_tenant_tables_require_row_scope(self):
        # 多租户表必须带行级过滤条件，防止白名单表内跨租户全表读取
        r = check_read_only("SELECT * FROM legal_cases", allowed_tables={"legal_cases"})
        self.assertEqual(r.error_code, "SQL_TENANT_SCOPE_REQUIRED")
        ok = check_read_only(
            "SELECT * FROM legal_cases WHERE organization_id = 5",
            allowed_tables={"legal_cases"},
        )
        self.assertTrue(ok.ok)
        # 普通表白名单不受行级过滤约束
        self.assertTrue(
            check_read_only("SELECT * FROM pub_tbl", allowed_tables={"pub_tbl"}).ok
        )
        # 任务表按 user_id 过滤可通过
        self.assertTrue(
            check_read_only(
                "SELECT * FROM tasks WHERE user_id = 7",
                allowed_tables={"tasks"},
            ).ok
        )

    def test_guard_error_code_contract(self):
        with self.assertRaises(SqlGuardError) as ctx:
            from app.mcp.sql_guard import _parse_single

            _parse_single("SELECT 1; DROP TABLE t")
        self.assertEqual(ctx.exception.code, "SQL_MULTI_STATEMENT")


class SqlStatementTimeoutTests(unittest.TestCase):
    """SQL_QUERY_TIMEOUT_SECONDS 必须下推到数据库。

    行数/字符数上限只约束「读回多少」，管不了「数据库算多久」；run() 走
    asyncio.to_thread，线程不可取消，所以 Agent 侧超时返回之后查询仍在库里跑。
    此前没有任何代码读这个配置。
    """

    def test_mysql_sets_and_restores_session_limit(self):
        set_stmts, reset_stmts = _statement_timeout_statements("mysql", 10)
        self.assertEqual(set_stmts, ["SET @@SESSION.MAX_EXECUTION_TIME = 10000"])
        # 必须还原：未配置 SQL_DATABASE_URL 时这条连接来自应用主池
        self.assertEqual(len(reset_stmts), 1)
        self.assertIn("MAX_EXECUTION_TIME", reset_stmts[0])

    def test_postgres_uses_transaction_scoped_setting(self):
        set_stmts, reset_stmts = _statement_timeout_statements("postgresql", 3)
        self.assertEqual(set_stmts, ["SET LOCAL statement_timeout = 3000"])
        self.assertEqual(reset_stmts, [])  # 事务级，随 rollback 自动失效

    def test_mariadb_uses_its_own_variable_and_unit(self):
        """MariaDB 没有 MAX_EXECUTION_TIME：照 MySQL 发过去会报未知变量，把查询打成失败。"""
        set_stmts, reset_stmts = _statement_timeout_statements("mysql", 10, is_mariadb=True)
        self.assertEqual(set_stmts, ["SET @@SESSION.max_statement_time = 10"])  # 单位是秒
        self.assertEqual(reset_stmts, ["SET @@SESSION.max_statement_time = @@GLOBAL.max_statement_time"])

    def test_unsupported_dialect_yields_no_statements(self):
        self.assertEqual(_statement_timeout_statements("sqlite", 10), ([], []))


class SqlToolExecutionTests(unittest.TestCase):
    """_execute 的超时下推与错误面：不假装超时已生效，也不把 SQL 原文回传。"""

    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.admin = User(username="root", email="root@example.com", hashed_password="h", role="admin")
        self.db.add(self.admin)
        self.db.commit()
        self.tool = SQLTool()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_result_reports_timeout_budget_and_enforcement(self):
        with patch.object(SQLTool, "_read_engine", staticmethod(lambda: self.engine)), patch.object(
            get_settings(), "SQL_QUERY_TIMEOUT_SECONDS", 7
        ):
            payload = self.tool._execute("SELECT 1 AS one", user_id=self.admin.id, db=self.db)
        self.assertEqual(payload["timeout_seconds"], 7)
        # sqlite 无法下推 → 如实标 false，而不是让调用方以为有上限
        self.assertFalse(payload["timeout_enforced"])
        self.assertEqual(payload["returned_count"], 1)

    def test_mysql_dialect_wraps_query_with_timeout_statements(self):
        executed: list[str] = []

        class _FakeResult:
            def mappings(self):
                return iter(())

        class _FakeConn:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def execute(self_inner, clause, *args, **kwargs):
                executed.append(str(clause))
                return _FakeResult()

        fake_engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"), connect=lambda: _FakeConn())
        with patch.object(SQLTool, "_read_engine", staticmethod(lambda: fake_engine)), patch.object(
            get_settings(), "SQL_QUERY_TIMEOUT_SECONDS", 4
        ):
            payload = self.tool._execute("SELECT 1 AS one", user_id=self.admin.id, db=self.db)
        self.assertTrue(payload["timeout_enforced"])
        self.assertEqual(len(executed), 3)  # set → query → restore
        self.assertIn("MAX_EXECUTION_TIME = 4000", executed[0])
        self.assertIn("SELECT 1", executed[1])
        self.assertIn("MAX_EXECUTION_TIME", executed[2])

    def test_timeout_statement_restored_even_when_query_fails(self):
        executed: list[str] = []

        class _FakeConn:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def execute(self_inner, clause, *args, **kwargs):
                text_clause = str(clause)
                executed.append(text_clause)
                if text_clause.strip().upper().startswith("SELECT"):
                    raise RuntimeError("boom")
                return None

        fake_engine = SimpleNamespace(dialect=SimpleNamespace(name="mysql"), connect=lambda: _FakeConn())
        with patch.object(SQLTool, "_read_engine", staticmethod(lambda: fake_engine)), self.assertRaises(
            RuntimeError
        ):
            self.tool._execute("SELECT 1", user_id=self.admin.id, db=self.db)
        self.assertIn("MAX_EXECUTION_TIME", executed[-1])  # 失败也要还原，别留给下一个借用者


class SqlToolErrorSurfaceTests(unittest.TestCase):
    """驱动异常不得把 SQL 原文和字面量带回观察值（契约已把 sql 列为 sensitive_fields）。"""

    SECRET_SQL = "SELECT * FROM users WHERE id_card = '110101199001011234'"

    def _run(self, exc: Exception) -> dict:
        tool = SQLTool()
        with patch.object(SQLTool, "_execute", side_effect=exc):
            return asyncio.run(tool.run(sql=self.SECRET_SQL, user_id=1, db=None))

    def test_mysql_max_execution_time_maps_to_timeout_code(self):
        orig = Exception(3024, "Query execution was interrupted, maximum statement execution time exceeded")
        result = self._run(OperationalError(self.SECRET_SQL, {}, orig))
        self.assertFalse(result["success"])
        self.assertEqual(result["data"]["sql_error_code"], "sql_query_timeout")

    def test_postgres_query_canceled_maps_to_timeout_code(self):
        orig = Exception("canceling statement due to statement timeout")
        orig.sqlstate = "57014"
        result = self._run(OperationalError(self.SECRET_SQL, {}, orig))
        self.assertEqual(result["data"]["sql_error_code"], "sql_query_timeout")

    def test_generic_failure_keeps_generic_code(self):
        result = self._run(OperationalError(self.SECRET_SQL, {}, Exception("no such table")))
        self.assertEqual(result["data"]["sql_error_code"], "sql_query_failed")

    def test_error_payload_never_contains_the_statement(self):
        for exc in (
            OperationalError(self.SECRET_SQL, {}, Exception(3024, "timeout")),
            OperationalError(self.SECRET_SQL, {}, Exception("no such table")),
        ):
            result = self._run(exc)
            serialized = json.dumps(result, ensure_ascii=False, default=str)
            self.assertNotIn("110101199001011234", serialized)
            self.assertNotIn("id_card", serialized)


if __name__ == "__main__":
    unittest.main()
