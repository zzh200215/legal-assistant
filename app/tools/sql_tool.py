import asyncio

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import engine
from app.models.user import User
from app.tools.base import BaseAgentTool, tool_error, tool_success


class SQLTool(BaseAgentTool):
    name = "sql_query_tool"
    description = "执行只读 SQL 查询，仅允许 SELECT 语句。"
    auto_context_fields = ("user_id", "db")
    parameters = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "SQL SELECT 语句"},
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

    def _execute(self, sql: str, *, user_id: int | None, db: Session | None) -> list[dict]:
        self._ensure_admin(user_id, db)
        normalized_sql = (sql or "").strip()
        sql_upper = normalized_sql.upper()
        if not sql_upper.startswith("SELECT"):
            raise PermissionError("Only SELECT queries are allowed")
        if ";" in normalized_sql:
            raise PermissionError("Multiple SQL statements are not allowed")
        forbidden_keywords = ("INFORMATION_SCHEMA", "PG_CATALOG", "MYSQL.", "SQLITE_MASTER", "SQLITE_SCHEMA")
        if any(keyword in sql_upper for keyword in forbidden_keywords):
            raise PermissionError("System catalog queries are not allowed")

        with engine.connect() as conn:
            result = conn.execute(text(normalized_sql))
            return [dict(row._mapping) for row in result]

    async def run(self, sql: str, user_id: int | None = None, db: Session | None = None) -> dict:
        try:
            rows = await asyncio.to_thread(self._execute, sql, user_id=user_id, db=db)
            return tool_success(
                f"查询到 {len(rows)} 条记录",
                {
                    "rows": rows[:50],
                    "returned_count": min(len(rows), 50),
                    "total_count": len(rows),
                },
            )
        except Exception as e:
            return tool_error("SQL 查询失败", str(e))
