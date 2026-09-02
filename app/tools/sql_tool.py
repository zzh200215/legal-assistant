import asyncio
import logging
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine as _app_engine
from app.mcp.sql_guard import SqlGuardError, check_read_only, redact_rows
from app.mcp.tool_contract import ToolContract
from app.models.user import User
from app.tools.base import BaseAgentTool, tool_error, tool_success

logger = logging.getLogger("app.tools.sql_tool")

_TIMEOUT_UNSUPPORTED_LOGGED: set[str] = set()


def _statement_timeout_statements(
    dialect_name: str, timeout_seconds: int, *, is_mariadb: bool = False
) -> tuple[list[str], list[str]]:
    """按方言返回 (设置语句, 还原语句)；方言不支持时返回 ([], [])。

    SQL_QUERY_TIMEOUT_SECONDS 此前只是个声明——没有任何代码读它。行数/字符数上限只
    约束「读回多少」，管不了「数据库算多久」；而 run() 走 asyncio.to_thread，线程不可
    取消，所以 Agent 侧超时返回之后，这条查询仍在数据库里继续跑、继续占连接。上限必须
    下推到数据库本身。
    """
    ms = max(1, int(round(timeout_seconds * 1000)))
    if dialect_name.startswith("postgres"):
        # 事务级：随 commit/rollback 自动失效，不会污染池里这条连接。
        return [f"SET LOCAL statement_timeout = {ms}"], []
    if dialect_name.startswith("mysql"):
        # MySQL 与 MariaDB 变量名、单位都不同：MariaDB 没有 MAX_EXECUTION_TIME，
        # 照 MySQL 发过去会直接报未知变量，把本来能跑的查询打成失败。
        # 两者都只有 session 级，用完必须还原：未配置 SQL_DATABASE_URL 时这条连接来自
        # 应用主池，留下一个 10s 上限会连带掐掉后续正常业务查询。
        variable = "max_statement_time" if is_mariadb else "MAX_EXECUTION_TIME"
        value = f"{ms / 1000:g}" if is_mariadb else str(ms)  # MariaDB 单位是秒
        return (
            [f"SET @@SESSION.{variable} = {value}"],
            [f"SET @@SESSION.{variable} = @@GLOBAL.{variable}"],
        )
    return [], []


def _warn_timeout_unsupported(dialect_name: str) -> None:
    """每个方言只告警一次：不静默假装超时已生效。"""
    if dialect_name in _TIMEOUT_UNSUPPORTED_LOGGED:
        return
    _TIMEOUT_UNSUPPORTED_LOGGED.add(dialect_name)
    logger.warning(
        "SQL_QUERY_TIMEOUT_SECONDS 无法在 %s 方言上下推为语句超时，"
        "本次查询只受行数/字符数上限约束（结果里 timeout_enforced=false）。",
        dialect_name,
    )


def _db_error_code(exc: Exception) -> str:
    """把驱动异常收敛成稳定错误码：语句超时单独成码，其余归入通用失败。

    MySQL 触发 MAX_EXECUTION_TIME 报 errno 3024，PostgreSQL 触发 statement_timeout
    报 SQLSTATE 57014，两者都会被 SQLAlchemy 包成 OperationalError。
    """
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ()) or ()
    mysql_errno = args[0] if args and isinstance(args[0], int) else None
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if mysql_errno == 3024 or sqlstate == "57014":
        return "sql_query_timeout"
    return "sql_query_failed"


class SQLTool(BaseAgentTool):
    name = "sql_query_tool"
    description = "执行只读 SQL 查询，仅允许单条 SELECT / WITH…SELECT，限制在配置白名单内。"
    auto_context_fields = ("user_id", "db")
    contract = ToolContract(
        name="sql_query_tool", read_only=True, requires_approval=True,
        side_effect="reads_sql", max_retries=0, retryable=False,
        idempotency_keyed=False, safely_retryable=False,
        audit_level="summary", sensitive_fields=("sql",),
    )
    parameters = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "只读 SQL SELECT 语句"},
        },
        "required": ["sql"],
    }

    @staticmethod
    def _ensure_admin(user_id: int | None, db: Session | None) -> None:
        if user_id is None or db is None:
            raise PermissionError("SQL query requires authenticated admin context")
        user = db.get(User, user_id)
        if not user or user.role != "admin":
            raise PermissionError("Only admin users can execute SQL queries")

    @staticmethod
    def _read_engine():
        """优先使用配置的只读账号连接；未配置时回退应用引擎并显式告警。"""
        settings = get_settings()
        readonly_url = (settings.SQL_DATABASE_URL or "").strip()
        if readonly_url:
            return create_engine(
                readonly_url,
                pool_pre_ping=True,
                pool_size=settings.DATABASE_POOL_SIZE,
                max_overflow=settings.DATABASE_POOL_MAX_OVERFLOW,
            )
        if settings.SQL_ENFORCE_READONLY_ACCOUNT:
            # 部署要求：SQL_DATABASE_URL 必须指向只读账号；当前仅告警，不静默放行。
            logger.warning(
                "SQL_ENFORCE_READONLY_ACCOUNT=true 但未配置 SQL_DATABASE_URL，"
                "SQLTool 将使用应用主账号连接（仅 SELECT），请部署独立只读账号。"
            )
        return _app_engine

    def _execute(self, sql: str, *, user_id: int | None, db: Session | None) -> dict:
        self._ensure_admin(user_id, db)
        settings = get_settings()
        allowed_schemas = {
            item.strip().lower()
            for item in (settings.SQL_ALLOWED_SCHEMAS or "").split(",")
            if item.strip()
        }
        allowed_tables = {
            item.strip().lower()
            for item in (settings.SQL_ALLOWED_TABLES or "").split(",")
            if item.strip()
        }
        redact_columns = {
            item.strip().lower()
            for item in (settings.SQL_REDACT_COLUMNS or "").split(",")
            if item.strip()
        }

        result = check_read_only(
            sql,
            allowed_schemas=allowed_schemas,
            allowed_tables=allowed_tables,
        )
        if not result.ok:
            raise SqlGuardError(result.error_code or "SQL_NOT_READ_ONLY", result.reason or "SQL 校验失败")

        max_rows = settings.SQL_QUERY_MAX_ROWS
        max_chars = settings.SQL_RESULT_MAX_CHARS
        timeout_seconds = int(settings.SQL_QUERY_TIMEOUT_SECONDS)
        started = time.time()
        engine = self._read_engine()
        set_timeout, reset_timeout = _statement_timeout_statements(
            engine.dialect.name,
            timeout_seconds,
            is_mariadb=bool(getattr(engine.dialect, "is_mariadb", False)),
        )
        if not set_timeout:
            _warn_timeout_unsupported(engine.dialect.name)
        rows: list[dict] = []
        truncated = False
        total_chars = 0
        with engine.connect() as conn:
            for statement in set_timeout:
                conn.execute(text(statement))
            try:
                res = conn.execute(text(sql).execution_options(stream_results=True))
                for row in res.mappings():
                    if len(rows) >= max_rows:
                        truncated = True
                        break
                    item: dict = {}
                    for key, value in row.items():
                        if value is None:
                            item[key] = None
                            continue
                        text_value = str(value)
                        room = max_chars - total_chars
                        if len(text_value) > room:
                            text_value = text_value[: max(0, room)]
                            truncated = True
                        total_chars += len(text_value)
                        item[key] = text_value
                    rows.append(item)
                    if total_chars >= max_chars:
                        truncated = True
                        break
            finally:
                for statement in reset_timeout:
                    try:
                        conn.execute(text(statement))
                    except Exception:  # noqa: BLE001 - 还原失败不能掩盖原始异常
                        logger.warning("恢复 %s 语句超时设置失败", engine.dialect.name)

        redacted = redact_rows(rows, redact_columns)
        return {
            "rows": redacted,
            "returned_count": len(redacted),
            "truncated": truncated,
            "duration_ms": int((time.time() - started) * 1000),
            "timeout_seconds": timeout_seconds,
            # 方言不支持下推时如实标 false，不假装超时已生效。
            "timeout_enforced": bool(set_timeout),
            "template": result.normalized_template,
            "param_hash": result.param_hash,
            "referenced_tables": result.referenced_tables,
        }

    async def run(self, sql: str, user_id: int | None = None, db: Session | None = None) -> dict:
        try:
            payload = await asyncio.to_thread(self._execute, sql, user_id=user_id, db=db)
            return tool_success(
                f"查询到 {payload['returned_count']} 条记录"
                + ("（结果已截断）" if payload["truncated"] else ""),
                payload,
            )
        except SqlGuardError as e:
            return tool_error("SQL 查询被安全策略拒绝", e.code, {"sql_guard_code": e.code})
        except PermissionError:
            return tool_error("SQL 查询无权限", "sql_permission_denied")
        except Exception as e:
            # 不回传 str(e)：SQLAlchemy 的 DBAPI 异常 str() 带 [SQL: ...] 与
            # [parameters: ...]，契约把 sql 列为 sensitive_fields 就是为了让语句里的
            # 字面量别进观察值和审计，错误分支不能从后门原样送回去。完整异常进服务端日志。
            code = _db_error_code(e)
            logger.warning("SQL 查询失败（%s）", code, exc_info=True)
            if code == "sql_query_timeout":
                return tool_error(
                    f"SQL 查询超过 {get_settings().SQL_QUERY_TIMEOUT_SECONDS} 秒上限，已被数据库中止",
                    code,
                    {"sql_error_code": code, "exception": type(e).__name__},
                )
            return tool_error(
                "SQL 查询失败", code, {"sql_error_code": code, "exception": type(e).__name__}
            )
