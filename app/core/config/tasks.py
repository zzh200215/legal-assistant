"""Background task / agent runtime settings."""

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class TaskSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    AGENT_TOOL_TIMEOUT_SECONDS: int = Field(default=45, ge=5, le=300)
    AGENT_PARALLEL_MAX_WORKERS: int = Field(default=2, ge=1, le=4)
