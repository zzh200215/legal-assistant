"""环境变量密钥提供方（SecretProvider 的 env 实现，本地开发安全默认）。

语义与既有配置完全兼容：
- 密钥环形态（如 ``legal_data_encryption``）：
  ``LEGAL_DATA_ENCRYPTION_KEYS_JSON``（多版本 JSON）+ ``LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION``；
  未配置环时单密钥 ``LEGAL_DATA_ENCRYPTION_KEY`` 视为 ``v1``。
- 普通密钥（如 ``LLM_API_KEY``）：按 ``name.upper()`` 直接读 settings 字段，
  单版本 ``current``。

所有读操作依赖注入的 settings（encryption._key 显式传入），保持既有测试
``patch(encryption.get_settings)`` 行为不变。
"""

from __future__ import annotations

import json
from typing import Any

from app.core.secrets.base import (
    KeyState,
    KeyVersion,
    SecretNotFoundError,
    SecretProvider,
    SECRET_LEGAL_DATA_ENCRYPTION,
)

# 密钥环形态的密钥：keys_json（多版本）/ active_version / single（单密钥降级为 v1）。
_RING_NAMES: dict[str, dict[str, str]] = {
    SECRET_LEGAL_DATA_ENCRYPTION: {
        "keys_json": "LEGAL_DATA_ENCRYPTION_KEYS_JSON",
        "active_version": "LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION",
        "single": "LEGAL_DATA_ENCRYPTION_KEY",
    },
}


class EnvSecretProvider(SecretProvider):
    """从环境变量/pydantic settings 读取密钥（本地与自托管默认提供方）。"""

    provider_name = "env"

    def __init__(self, settings: Any | None = None):
        # settings 为 None 时惰性取全局配置；调用方（encryption）显式注入以保持测试可 patch。
        self._settings_ref = settings
        self._settings: Any | None = None

    def _get_settings(self) -> Any:
        if self._settings is None:
            from app.core.config import get_settings

            self._settings = self._settings_ref or get_settings()
        return self._settings

    # ── 密钥环解析（与既有 encryption._key 语义一致）─────────────────────
    def _ring_and_active(self, name: str) -> tuple[dict[str, str] | None, str | None]:
        cfg = _RING_NAMES.get(name)
        if cfg is None:
            return None, None
        settings = self._get_settings()
        keys_json = str(getattr(settings, cfg["keys_json"], "") or "").strip()
        if keys_json:
            try:
                raw = json.loads(keys_json)
            except Exception:
                raw = None
            ring = (
                {str(k): str(v) for k, v in raw.items()}
                if isinstance(raw, dict)
                else {}
            )
            active = str(getattr(settings, cfg["active_version"], "") or "v1")
            return ring, active
        single = str(getattr(settings, cfg["single"], "") or "").strip()
        if single:
            return {"v1": single}, "v1"
        return {}, "v1"

    # ── SecretProvider 接口 ─────────────────────────────────────────────
    def get(self, name: str) -> str:
        ring, active = self._ring_and_active(name)
        if ring is not None:
            if not ring:
                raise SecretNotFoundError(f"密钥未配置：{name}")
            if active not in ring:
                raise SecretNotFoundError(f"激活版本不可用：{name}@{active}")
            return ring[active]
        raw = getattr(self._get_settings(), name.upper(), None)
        if raw is None or not str(raw).strip():
            raise SecretNotFoundError(f"密钥未配置：{name}")
        return str(raw)

    def get_version(self, name: str, version: str) -> str:
        ring, active = self._ring_and_active(name)
        if ring is not None:
            if version not in ring:
                raise SecretNotFoundError(f"密钥版本不可用：{name}@{version}")
            return ring[version]
        if str(version) == "current":
            return self.get(name)
        raise SecretNotFoundError(f"密钥版本不可用：{name}@{version}")

    def list_versions(self, name: str) -> tuple[KeyVersion, ...]:
        ring, active = self._ring_and_active(name)
        if ring is not None:
            if not ring:
                return ()
            return tuple(
                KeyVersion(
                    version=ver,
                    active=(ver == active),
                    state=KeyState.ACTIVE if ver == active else KeyState.PENDING_RETIREMENT,
                    available=True,
                )
                for ver in sorted(ring)
            )
        raw = getattr(self._get_settings(), name.upper(), None)
        if raw is None or not str(raw).strip():
            return ()
        return (KeyVersion(version="current", active=True, state=KeyState.ACTIVE, available=True),)

    def current_version(self, name: str) -> str | None:
        ring, active = self._ring_and_active(name)
        if ring is not None:
            return active if ring else None
        raw = getattr(self._get_settings(), name.upper(), None)
        return "current" if raw is not None and str(raw).strip() else None
