"""KMS / Secret Manager 适配器骨架（P1-A）。

**重要**：本模块是**可插拔骨架**，未接入任何真实云 KMS/Secret Manager。
未配置 ``SECRET_KMS_REGION``/``SECRET_KMS_ENDPOINT`` 时构造即抛
``SecretProviderNotConfiguredError``——显式拒绝，不伪造"已接入"。

真实接入（部署方职责，见 docs/secret-management.md「部署方需确认」）：
在下方标注的位置按云 SDK 实现 get/get_version/list_versions/current_version，
并补充对应测试后，方可把 ``SECRET_PROVIDER=kms`` 用于生产。
"""

from __future__ import annotations

from typing import Any

from app.core.secrets.base import (
    KeyVersion,
    SecretProvider,
    SecretProviderNotConfiguredError,
)


class KmsSecretProvider(SecretProvider):
    """云 KMS/Secret Manager 适配器骨架。

    - 构造校验 SECRET_KMS_REGION / SECRET_KMS_ENDPOINT；未配置 → 显式 not_configured。
    - 所有接口当前显式抛 not_configured，防止误以为已接入真实 KMS。
    """

    provider_name = "kms"

    def __init__(self, settings: Any | None = None):
        if settings is None:
            from app.core.config import get_settings

            settings = get_settings()
        self._region = str(getattr(settings, "SECRET_KMS_REGION", "") or "").strip()
        self._endpoint = str(getattr(settings, "SECRET_KMS_ENDPOINT", "") or "").strip()
        self._prefix = str(getattr(settings, "SECRET_KMS_PREFIX", "") or "aibg").strip()
        if not (self._region or self._endpoint):
            raise SecretProviderNotConfiguredError(
                "SECRET_KMS_REGION/SECRET_KMS_ENDPOINT 未配置：KMS 提供方未接入（可插拔骨架，"
                "未连接真实云 KMS）。请使用默认 env 提供方，或按 docs/secret-management.md "
                "完成真实接入后再切换 SECRET_PROVIDER=kms。"
            )

    def _not_configured(self, name: str) -> SecretProviderNotConfiguredError:
        return SecretProviderNotConfiguredError(
            f"KMS 提供方为可插拔骨架，未接入真实云服务（密钥：{name}）。"
            "请先在 KmsSecretProvider 中按云 SDK 实现并测试后启用。"
        )

    # 以下为真实云 SDK 接入点（部署方按阿里云 KMS / AWS Secrets Manager 等实现）。
    def get(self, name: str) -> str:
        raise self._not_configured(name)

    def get_version(self, name: str, version: str) -> str:
        raise self._not_configured(name)

    def list_versions(self, name: str) -> tuple[KeyVersion, ...]:
        raise self._not_configured(name)

    def current_version(self, name: str) -> str | None:
        raise self._not_configured(name)
