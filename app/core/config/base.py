"""Config package shared primitives.

All domain settings inherit from ``BaseSettings`` with the SAME flat env-var
names as the historical monolithic ``Settings``.  Each domain stays usable
standalone (tests construct a domain class directly); the final ``Settings``
composes them via multiple inheritance so ``settings.DATABASE_URL`` and friends
remain plain pydantic fields — no attribute forwarding magic.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

# Every domain class reads the same env file; keeping the config identical across
# bases avoids any MRO merge ambiguity in the final composed Settings class.
# extra="ignore" 与历史单体 Settings 行为一致：.env 中其它领域的变量被静默忽略，
# 使各领域类可独立实例化（测试/诊断需要）。
ENV_FILE_CONFIG = SettingsConfigDict(
    env_file=".env", env_file_encoding="utf-8", extra="ignore",
)


class CoreSettings(BaseSettings):
    """Runtime/global settings not owned by any single domain."""

    model_config = ENV_FILE_CONFIG

    ENVIRONMENT: str = "development"
    VITE_WS_HOST: str = "localhost:8001"
    # 开放平台异步任务：默认关闭，拒绝 /v1/contract-reviews 提交（任务消费者未上线）。
    OPEN_API_ENABLED: bool = False
