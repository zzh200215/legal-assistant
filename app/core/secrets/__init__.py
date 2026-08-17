"""统一密钥提供方（P1-A 密钥管理）。

- ``get_secret_provider(settings=None)``：按 ``SECRET_PROVIDER`` 选择提供方
  （默认 ``env``；``kms`` 未配置真实接入时构造即报错，不静默降级）。
- 导出：``SecretProvider`` / ``KeyVersion`` / ``RotationState`` / ``KeyState`` /
  稳定异常 / ``SECRET_LEGAL_DATA_ENCRYPTION`` / ``write_key_rotation_audit``。

设计约束：
- 密钥材料只在调用方内存中使用，绝不写入日志/审计/异常消息/API 响应。
- 本地开发默认 env 提供方（环境变量或密钥环 JSON），无需额外基础设施。
"""

from __future__ import annotations

from typing import Any

from app.core.secrets.base import (
    KeyState,
    KeyVersion,
    RotationState,
    SecretDecryptionError,
    SecretNotFoundError,
    SecretProvider,
    SecretProviderError,
    SecretProviderNotConfiguredError,
    SECRET_LEGAL_DATA_ENCRYPTION,
)
from app.core.secrets.env_provider import EnvSecretProvider
from app.core.secrets.kms_provider import KmsSecretProvider

__all__ = [
    "SecretProvider",
    "EnvSecretProvider",
    "KmsSecretProvider",
    "KeyState",
    "KeyVersion",
    "RotationState",
    "SecretProviderError",
    "SecretNotFoundError",
    "SecretDecryptionError",
    "SecretProviderNotConfiguredError",
    "SECRET_LEGAL_DATA_ENCRYPTION",
    "get_secret_provider",
]


def get_secret_provider(settings: Any | None = None) -> SecretProvider:
    """按配置选择密钥提供方；未指定时从 settings 读取（默认 env）。

    ``kms`` 未配置真实接入 → 构造即抛 SecretProviderNotConfiguredError
    （fail-safe，不静默回退 env，避免掩盖配置错误）。
    """
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    kind = str(getattr(settings, "SECRET_PROVIDER", "env") or "env").strip().lower()
    if kind == "kms":
        return KmsSecretProvider(settings=settings)
    return EnvSecretProvider(settings=settings)
