"""统一密钥提供方抽象（P1-A 密钥管理）。

- ``SecretProvider``：环境变量 / KMS / Secret Manager 的统一接口。
- ``KeyVersion`` / ``RotationState``：密钥版本、当前版本与轮换状态查询。
- 稳定异常：缺键 / 错误键 / 提供方未配置均 fail-closed，且异常信息
  **绝不携带密钥材料**（验收：密钥不写日志、不返回客户端）。

约束：
- 实现不得在异常消息、日志或返回结构中包含密钥明文。
- 未配置真实 KMS 时不得伪造"已接入"（KmsSecretProvider 构造即拒绝）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

# 已版本化的密钥名常量（密钥环形态）。
SECRET_LEGAL_DATA_ENCRYPTION = "legal_data_encryption"


class SecretProviderError(Exception):
    """密钥提供方错误基类。消息中禁止包含密钥材料。"""


class SecretNotFoundError(SecretProviderError, ValueError):
    """密钥或指定版本不存在（fail-closed；兼容既有 ValueError 契约）。"""


class SecretDecryptionError(SecretProviderError, ValueError):
    """密文解密失败：密钥错误、密文被篡改或格式损坏（fail-closed）。"""


class SecretProviderNotConfiguredError(SecretProviderError):
    """提供方（如 KMS）未配置真实接入：骨架语义，显式拒绝而非降级。"""


class KeyState(str, Enum):
    """密钥版本状态。"""

    ACTIVE = "active"  # 当前激活版本（新写入使用）
    PENDING_RETIREMENT = "pending_retirement"  # 保留用于旧密文解密，等待摘除
    RETIRED = "retired"  # 已摘除（不再可解析）


@dataclass(frozen=True)
class KeyVersion:
    """一个密钥版本的元数据（不含密钥明文）。"""

    version: str
    active: bool = False
    state: KeyState = KeyState.PENDING_RETIREMENT
    available: bool = True  # 密钥材料当前可解析


@dataclass(frozen=True)
class RotationState:
    """密钥的轮换状态摘要（审计/运营查询用，不含密钥明文）。"""

    provider: str
    name: str
    current_version: str | None
    versions: tuple[KeyVersion, ...]


class SecretProvider(ABC):
    """统一密钥提供方接口。

    实现约定：
    - ``get`` / ``get_version`` 返回密钥明文，仅限调用方内存使用；
    - 缺失/不可用必须抛 ``SecretNotFoundError``（不返回空串冒充成功）；
    - 版本/状态查询绝不返回密钥明文。
    """

    provider_name: str = "base"

    @abstractmethod
    def get(self, name: str) -> str:
        """返回 name 的当前激活版本密钥明文。"""

    @abstractmethod
    def get_version(self, name: str, version: str) -> str:
        """返回指定版本密钥明文；版本不存在抛 SecretNotFoundError。"""

    @abstractmethod
    def list_versions(self, name: str) -> tuple[KeyVersion, ...]:
        """列出已知版本与状态（激活/待摘除），不含密钥明文。"""

    @abstractmethod
    def current_version(self, name: str) -> str | None:
        """当前激活版本号；未配置返回 None。"""

    def rotation_state(self, name: str) -> RotationState:
        """轮换状态摘要（接口默认实现；子类可覆盖）。"""
        return RotationState(
            provider=self.provider_name,
            name=name,
            current_version=self.current_version(name),
            versions=self.list_versions(name),
        )
