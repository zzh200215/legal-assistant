"""P1-A 密钥管理：SecretProvider 接口 / env 实现 / 版本化 / 轮换状态 / KMS 骨架。

覆盖：
- env 实现：单密钥=v1、密钥环多版本、激活版本、get/get_version/list_versions/current_version。
- 缺失密钥 / 缺失版本 / 错误配置 → fail-closed（SecretNotFoundError，ValueError 子类）。
- rotation_state 摘要；异常消息不含密钥材料。
- 工厂选择：默认 env；SECRET_PROVIDER=kms 未配置 → 显式 not_configured（不伪造接入）；
  配置后为骨架，接口调用仍显式拒绝。
"""
import base64
import json
import unittest
from unittest.mock import patch

from app.core.secrets import (
    KeyState,
    SecretNotFoundError,
    SecretProviderNotConfiguredError,
    get_secret_provider,
)
from app.core.secrets.base import SECRET_LEGAL_DATA_ENCRYPTION
from app.core.secrets.env_provider import EnvSecretProvider
from app.core.secrets.kms_provider import KmsSecretProvider

K1 = base64.urlsafe_b64encode(b"1" * 32).decode("ascii")
K2 = base64.urlsafe_b64encode(b"2" * 32).decode("ascii")


class _FakeSettings:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class EnvSecretProviderTests(unittest.TestCase):
    def test_single_key_is_v1(self):
        provider = EnvSecretProvider(
            settings=_FakeSettings(
                LEGAL_DATA_ENCRYPTION_KEY=K1,
                LEGAL_DATA_ENCRYPTION_KEYS_JSON="",
                LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION="v1",
            )
        )
        self.assertEqual(provider.current_version(SECRET_LEGAL_DATA_ENCRYPTION), "v1")
        self.assertEqual(provider.get(SECRET_LEGAL_DATA_ENCRYPTION), K1)
        self.assertEqual(provider.get_version(SECRET_LEGAL_DATA_ENCRYPTION, "v1"), K1)

    def test_ring_multi_version_and_active(self):
        provider = EnvSecretProvider(
            settings=_FakeSettings(
                LEGAL_DATA_ENCRYPTION_KEY="",
                LEGAL_DATA_ENCRYPTION_KEYS_JSON=json.dumps({"v1": K1, "v2": K2}),
                LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION="v2",
            )
        )
        self.assertEqual(provider.current_version(SECRET_LEGAL_DATA_ENCRYPTION), "v2")
        self.assertEqual(provider.get(SECRET_LEGAL_DATA_ENCRYPTION), K2)
        # 旧版本在环内仍可解析（双密钥过渡窗口）
        self.assertEqual(provider.get_version(SECRET_LEGAL_DATA_ENCRYPTION, "v1"), K1)

    def test_list_versions_states_and_rotation_state(self):
        provider = EnvSecretProvider(
            settings=_FakeSettings(
                LEGAL_DATA_ENCRYPTION_KEY="",
                LEGAL_DATA_ENCRYPTION_KEYS_JSON=json.dumps({"v1": K1, "v2": K2}),
                LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION="v2",
            )
        )
        versions = provider.list_versions(SECRET_LEGAL_DATA_ENCRYPTION)
        by_version = {item.version: item for item in versions}
        self.assertEqual(set(by_version), {"v1", "v2"})
        self.assertTrue(by_version["v2"].active)
        self.assertEqual(by_version["v2"].state, KeyState.ACTIVE)
        self.assertFalse(by_version["v1"].active)
        self.assertEqual(by_version["v1"].state, KeyState.PENDING_RETIREMENT)

        state = provider.rotation_state(SECRET_LEGAL_DATA_ENCRYPTION)
        self.assertEqual(state.provider, "env")
        self.assertEqual(state.current_version, "v2")
        self.assertEqual(len(state.versions), 2)

    def test_missing_version_fails_closed(self):
        provider = EnvSecretProvider(
            settings=_FakeSettings(
                LEGAL_DATA_ENCRYPTION_KEY="",
                LEGAL_DATA_ENCRYPTION_KEYS_JSON=json.dumps({"v2": K2}),
                LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION="v2",
            )
        )
        with self.assertRaises(SecretNotFoundError):
            provider.get_version(SECRET_LEGAL_DATA_ENCRYPTION, "v1")

    def test_missing_key_fails_closed(self):
        provider = EnvSecretProvider(
            settings=_FakeSettings(
                LEGAL_DATA_ENCRYPTION_KEY="",
                LEGAL_DATA_ENCRYPTION_KEYS_JSON="",
                LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION="v1",
            )
        )
        with self.assertRaises(SecretNotFoundError):
            provider.get(SECRET_LEGAL_DATA_ENCRYPTION)
        self.assertIsNone(provider.current_version(SECRET_LEGAL_DATA_ENCRYPTION))
        self.assertEqual(provider.list_versions(SECRET_LEGAL_DATA_ENCRYPTION), ())

    def test_error_messages_never_contain_key_material(self):
        provider = EnvSecretProvider(
            settings=_FakeSettings(
                LEGAL_DATA_ENCRYPTION_KEY="",
                LEGAL_DATA_ENCRYPTION_KEYS_JSON=json.dumps({"v2": K2}),
                LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION="v2",
            )
        )
        with self.assertRaises(SecretNotFoundError) as ctx:
            provider.get_version(SECRET_LEGAL_DATA_ENCRYPTION, "v1")
        self.assertNotIn(K2, str(ctx.exception))
        self.assertNotIn(K1, str(ctx.exception))

    def test_plain_secret_name_reads_settings_attribute(self):
        provider = EnvSecretProvider(settings=_FakeSettings(LLM_API_KEY="test-key-value"))
        self.assertEqual(provider.get("LLM_API_KEY"), "test-key-value")
        self.assertEqual(provider.current_version("LLM_API_KEY"), "current")
        with self.assertRaises(SecretNotFoundError):
            provider.get("NOT_CONFIGURED_SECRET")

    def test_factory_defaults_to_env(self):
        self.assertIsInstance(get_secret_provider(settings=_FakeSettings()), EnvSecretProvider)

    def test_factory_kms_without_config_raises_not_configured(self):
        settings = _FakeSettings(SECRET_PROVIDER="kms", SECRET_KMS_REGION="", SECRET_KMS_ENDPOINT="")
        with self.assertRaises(SecretProviderNotConfiguredError):
            get_secret_provider(settings=settings)

    def test_kms_skeleton_rejects_calls_even_when_configured(self):
        settings = _FakeSettings(
            SECRET_PROVIDER="kms",
            SECRET_KMS_REGION="cn-hangzhou",
            SECRET_KMS_ENDPOINT="kms.cn-hangzhou.aliyuncs.com",
        )
        provider = get_secret_provider(settings=settings)
        self.assertIsInstance(provider, KmsSecretProvider)
        with self.assertRaises(SecretProviderNotConfiguredError):
            provider.get(SECRET_LEGAL_DATA_ENCRYPTION)


if __name__ == "__main__":
    unittest.main()
