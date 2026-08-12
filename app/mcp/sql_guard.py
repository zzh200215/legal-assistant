"""SQLTool 只读安全边界（AST 级，非正则）。

安全模型
--------
- 解析边界：使用 sqlglot 解析 AST。仅允许根节点为 ``Select``（含 WITH…SELECT）的
  **单条**语句；拒绝 INSERT/UPDATE/DELETE/MERGE/DDL/DCL/事务控制/存储过程
  （``Command``/``Grant``/…）/多语句（``;`` 分隔）／任何嵌入的写节点。注释作为解析器
  trivia 处理，不存在注释绕过。
- 白名单：``SQL_ALLOWED_SCHEMAS`` / ``SQL_ALLOWED_TABLES`` 非空时，引用的 schema/表
  必须命中；系统目录表（information_schema / pg_catalog / mysql.* / sqlite_master）一律拒绝。
- 危险函数/副作用：拒绝 ``Command`` 节点；块名单覆盖 LOAD_FILE/SLEEP/BENCHMARK 等
  文件/网络副作用函数。
- 审计：只产出“规范化 SQL 模板 + 参数哈希”，不保留原始字面量参数。
- 脱敏：``SQL_REDACT_COLUMNS`` 命中的输出列值替换为 ``****``。

注意：本模块只做静态校验与规范化，不执行 SQL；超时/行数/字节上限由执行层（SQLTool +
ToolExecutor）强制。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable

import sqlglot
from sqlglot import exp

# 根节点必须为 Select；其余一律拒绝。
_ALLOWED_ROOT = {exp.Select}

# 树中任何位置都不允许出现的写/控制/元数据节点类型（按节点类名匹配，防 API 漂移）。
_FORBIDDEN_NODE_NAMES = frozenset(
    {
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "TruncateTable",
        "Grant",
        "Revoke",
        "Command",
        "Set",
        "Use",
        "Transaction",
        "Copy",
        "Comment",
        "Into",
        "Lock",
    }
)

# 系统目录库/表：一律拒绝（防越权读取账号信息）。
_BLOCKED_SCHEMAS = frozenset({"information_schema", "pg_catalog", "mysql", "sys"})
_BLOCKED_TABLES = frozenset({"sqlite_master", "sqlite_sequence", "pg_tables", "pg_stat_activity"})

# 文件/网络/资源消耗副作用函数（大小写不敏感）。
_BLOCKED_FUNCTIONS = frozenset(
    {
        "load_file",
        "sleep",
        "benchmark",
        "sys_eval",
        "sys_exec",
        "xp_cmdshell",
        "openrowset",
        "opendatasource",
    }
)


class SqlGuardError(ValueError):
    """SQL 安全校验失败（携带错误码，供 API/执行器映射）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass
class SqlCheckResult:
    ok: bool
    reason: str | None = None
    root_type: str | None = None
    normalized_template: str | None = None
    param_hash: str | None = None
    referenced_tables: list[str] = field(default_factory=list)
    error_code: str | None = None


def _parse_single(sql: str) -> exp.Expression:
    if not sql or not sql.strip():
        raise SqlGuardError("SQL_EMPTY", "SQL 语句不能为空")
    try:
        statements = sqlglot.parse(sql)
    except Exception as exc:  # noqa: BLE001 - 解析失败一律拒绝
        raise SqlGuardError("SQL_PARSE_ERROR", "SQL 无法解析") from exc
    if not statements:
        raise SqlGuardError("SQL_EMPTY", "SQL 语句不能为空")
    if len(statements) > 1:
        raise SqlGuardError("SQL_MULTI_STATEMENT", "仅允许单条 SQL 语句")
    return statements[0]


def _referenced_tables(root: exp.Expression) -> list[str]:
    tables: list[str] = []
    for node in root.walk():
        if isinstance(node, exp.Table):
            name = str(node.name or "").strip().lower()
            if name:
                tables.append(name)
    return tables


def _check_table_scope(
    root: exp.Expression,
    *,
    allowed_schemas: set[str],
    allowed_tables: set[str],
) -> None:
    for node in root.walk():
        if not isinstance(node, exp.Table):
            continue
        schema = str(node.db or "").strip().lower()
        name = str(node.name or "").strip().lower()
        if schema in _BLOCKED_SCHEMAS or name in _BLOCKED_TABLES:
            raise SqlGuardError(
                "SQL_CATALOG_DENIED", "禁止查询系统目录或系统表"
            )
        if allowed_tables and name and name not in allowed_tables:
            raise SqlGuardError(
                "SQL_TABLE_DENIED", f"表 {name} 不在白名单内"
            )
        if allowed_schemas:
            if not schema or schema not in allowed_schemas:
                raise SqlGuardError(
                    "SQL_SCHEMA_DENIED", f"schema {schema or '<默认>'} 不在白名单内"
                )


def _check_functions(root: exp.Expression) -> None:
    for node in root.walk():
        if isinstance(node, exp.Anonymous):
            func_name = str(node.this or "").strip().lower()
            if func_name in _BLOCKED_FUNCTIONS:
                raise SqlGuardError("SQL_DANGEROUS_FUNCTION", f"禁止调用函数 {func_name}")
        elif isinstance(node, exp.Func):
            func_name = type(node).__name__.lower()
            if func_name in _BLOCKED_FUNCTIONS:
                raise SqlGuardError("SQL_DANGEROUS_FUNCTION", f"禁止调用函数 {func_name}")


def _normalize_template(root: exp.Expression) -> tuple[str, str]:
    """把字面量替换为占位符，生成规范化模板并求参数哈希（审计用，不保留原文）。"""
    for node in root.walk():
        if isinstance(node, exp.Literal) and not isinstance(node, exp.Null):
            node.replace(exp.Placeholder())
    try:
        template = " ".join(sqlglot.transpile(str(root), read=sqlglot.Dialect.get_or_raise("mysql"))[0].split())
    except Exception:  # noqa: BLE001 - 规范化失败不阻断（模板尽力而为）
        template = str(root)
    digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return template, digest


def check_read_only(
    sql: str,
    *,
    allowed_schemas: set[str] | None = None,
    allowed_tables: set[str] | None = None,
) -> SqlCheckResult:
    """校验 SQL 为单条只读 SELECT，并做 schema/表白名单检查。不执行。"""
    try:
        root = _parse_single(sql)
    except SqlGuardError as exc:
        return SqlCheckResult(ok=False, reason=str(exc), error_code=exc.code)

    root_type = type(root).__name__
    if not isinstance(root, tuple(_ALLOWED_ROOT)):
        return SqlCheckResult(
            ok=False,
            reason="仅允许 SELECT / WITH…SELECT 查询",
            root_type=root_type,
            error_code="SQL_NOT_READ_ONLY",
        )
    for node in root.walk():
        node_name = type(node).__name__
        if node_name in _FORBIDDEN_NODE_NAMES:
            return SqlCheckResult(
                ok=False,
                reason=f"SQL 包含不允许的节点：{node_name}",
                root_type=root_type,
                error_code="SQL_NOT_READ_ONLY",
            )
    try:
        _check_table_scope(
            root,
            allowed_schemas=set(allowed_schemas or ()),
            allowed_tables=set(allowed_tables or ()),
        )
        _check_functions(root)
    except SqlGuardError as exc:
        return SqlCheckResult(ok=False, reason=str(exc), root_type=root_type, error_code=exc.code)

    template, digest = _normalize_template(root)
    return SqlCheckResult(
        ok=True,
        root_type=root_type,
        normalized_template=template,
        param_hash=digest,
        referenced_tables=_referenced_tables(root),
    )


def redact_rows(rows: Iterable[dict[str, Any]], redact_columns: set[str]) -> list[dict[str, Any]]:
    """把命中的敏感列输出值替换为脱敏标记；未命中列原样返回。"""
    if not redact_columns:
        return list(rows)
    redacted = []
    for row in rows:
        safe = {}
        for key, value in row.items():
            if key.strip().lower() in redact_columns:
                safe[key] = "****"
            else:
                safe[key] = value
        redacted.append(safe)
    return redacted
