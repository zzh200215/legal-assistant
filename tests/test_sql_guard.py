"""SQLTool 只读安全边界：AST 级校验拒绝写/多语句/越权/危险函数/目录，白名单、脱敏。"""

import unittest

from app.mcp.sql_guard import SqlGuardError, check_read_only, redact_rows


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

    def test_guard_error_code_contract(self):
        with self.assertRaises(SqlGuardError) as ctx:
            from app.mcp.sql_guard import _parse_single

            _parse_single("SELECT 1; DROP TABLE t")
        self.assertEqual(ctx.exception.code, "SQL_MULTI_STATEMENT")


if __name__ == "__main__":
    unittest.main()
