"""SQLTool 安全配置：白名单、上限、脱敏与只读账号要求。"""

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class SQLSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    SQL_QUERY_TIMEOUT_SECONDS: int = Field(default=10, ge=1, le=300)
    SQL_QUERY_MAX_ROWS: int = Field(default=200, ge=1, le=10000)
    SQL_RESULT_MAX_CHARS: int = Field(default=100_000, ge=1000, le=5_000_000)
    # 逗号分隔的 schema / 表白名单；为空表示不按此维度限制（生产必须配置）。
    SQL_ALLOWED_SCHEMAS: str = ""
    SQL_ALLOWED_TABLES: str = ""
    # 逗号分隔的敏感列名：输出值脱敏为 ****。
    SQL_REDACT_COLUMNS: str = ""
    # 可选：独立只读数据库账号连接串。未配置且 ENFORCE 开启时使用应用主账号并显式告警。
    SQL_DATABASE_URL: str = ""
    SQL_ENFORCE_READONLY_ACCOUNT: bool = Field(default=True)
