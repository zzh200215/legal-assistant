import asyncio
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine as _app_engine
from app.mcp.sql_guard import SqlGuardError, check_read_only, redact_rows
from app.mcp.tool_contract import ToolContract
from app.models.user import User
from app.tools.base import BaseAgentTool, tool_error, tool_success


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
            import logging

            logging.getLogger("app.tools.sql_tool").warning(
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
        started = time.time()
        engine = self._read_engine()
        rows: list[dict] = []
        truncated = False
        total_chars = 0
        with engine.connect() as conn:
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

        redacted = redact_rows(rows, redact_columns)
        return {
            "rows": redacted,
            "returned_count": len(redacted),
            "truncated": truncated,
            "duration_ms": int((time.time() - started) * 1000),
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
        except PermissionError as e:
            return tool_error("SQL 查询无权限", "sql_permission_denied")
        except Exception as e:
            return tool_error("SQL 查询失败", "sql_query_failed", {"detail": str(e)})
