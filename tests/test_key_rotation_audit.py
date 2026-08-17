"""P1-A 密钥轮换：错误密钥 fail-closed / 轮换审计脱敏 / 受控摘除门禁。

覆盖：
- 版本存在但密钥错误 → 解密失败（SecretDecryptionError，ValueError 子类），
  异常消息不含密钥材料；
- 轮换审计事件（key_rotation）只含版本/统计元数据，绝不包含密钥原文；
- 审计写失败降级不阻断轮换主流程；
- validate_key_retirement 四道门禁（版本存在/非激活/可解密/无残留密文）。
"""
import base64
import json
import unittest
from unittest.mock import patch

from app.core import encryption
from app.core.secrets.audit import write_key_rotation_audit
from app.core.secrets.base import SecretDecryptionError, SECRET_LEGAL_DATA_ENCRYPTION
from app.core.secrets.rotation import validate_key_retirement

K1 = base64.urlsafe_b64encode(b"1" * 32).decode("ascii")
K2 = base64.urlsafe_b64encode(b"2" * 32).decode("ascii")


class _FakeSettings:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class WrongKeyFailClosedTests(unittest.TestCase):
    def test_wrong_key_for_existing_version_fails_closed(self):
        with patch.object(
            encryption,
            "get_settings",
            return_value=_FakeSettings(
                LEGAL_DATA_ENCRYPTION_KEY="",
                LEGAL_DATA_ENCRYPTION_KEYS_JSON=json.dumps({"v1": K1}),
                LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION="v1",
            ),
        ):
            cipher = encryption.encrypt_text("机密合同内容")

        # 同一版本号但密钥错误（模拟误配/密钥被替换）：必须解密失败且不泄露密钥。
        with patch.object(
            encryption,
            "get_settings",
            return_value=_FakeSettings(
                LEGAL_DATA_ENCRYPTION_KEY="",
                LEGAL_DATA_ENCRYPTION_KEYS_JSON=json.dumps({"v1": K2}),
                LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION="v1",
            ),
        ):
            with self.assertRaises(SecretDecryptionError) as ctx:
                encryption.decrypt_text(cipher)
            self.assertNotIn(K1, str(ctx.exception))
            self.assertNotIn(K2, str(ctx.exception))
            self.assertNotIn("机密合同内容", str(ctx.exception))

    def test_missing_version_error_does_not_leak_key(self):
        with patch.object(
            encryption,
            "get_settings",
            return_value=_FakeSettings(
                LEGAL_DATA_ENCRYPTION_KEY="",
                LEGAL_DATA_ENCRYPTION_KEYS_JSON=json.dumps({"v2": K2}),
                LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION="v2",
            ),
        ):
            with self.assertRaises(ValueError) as ctx:
                encryption.decrypt_text("enc:v1:AAEBAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f")
            self.assertNotIn(K2, str(ctx.exception))


class RotationAuditTests(unittest.TestCase):
    def test_audit_event_carries_metadata_only_no_key_material(self):
        captured = {}

        def fake_write_event(**kwargs):
            captured.update(kwargs)
            return None

        with patch(
            "app.services.org.security_audit_service.write_event",
            side_effect=fake_write_event,
        ):
            write_key_rotation_audit(
                action="rotate",
                result="success",
                target_version="v2",
                sanitized_metadata={
                    "old_versions": ["v1"],
                    "new_version": "v2",
                    "rewritten_columns": {"legal.consultation_content": 12},
                    "decryptable_after": 100,
                },
            )

        self.assertEqual(captured["event_type"], "key_rotation")
        self.assertEqual(captured["action"], "rotate")
        self.assertEqual(captured["result"], "success")
        self.assertEqual(captured["target_type"], "data_encryption_key")
        self.assertEqual(captured["target_id"], "v2")
        serialized = captured["sanitized_metadata"]
        self.assertNotIn(K1, serialized)
        self.assertNotIn(K2, serialized)
        self.assertIn('"new_version": "v2"', serialized)

    def test_audit_write_failure_degrades_without_raising(self):
        with patch(
            "app.services.org.security_audit_service.write_event",
            side_effect=RuntimeError("db down"),
        ):
            # 审计写失败不阻断轮换主流程（记录 stderr 告警），也不抛错。
            write_key_rotation_audit(
                action="retire", result="success", target_version="v1",
                sanitized_metadata={"remaining_versions": ["v2"]},
            )


class RetirementGateTests(unittest.TestCase):
    def _column_state(self, versions=None):
        return {
            "legal.consultation_content": {
                "rows": 3,
                "versions": versions or {"v2": 3},
            }
        }

    def test_accepts_when_all_gates_pass(self):
        reasons = validate_key_retirement(
            version="v1",
            ring={"v1": K1, "v2": K2},
            active_version="v2",
            column_state=self._column_state({"v2": 3}),
            decrypt_failures=[],
        )
        self.assertEqual(reasons, [])

    def test_rejects_unknown_version(self):
        reasons = validate_key_retirement(
            version="v9", ring={"v1": K1, "v2": K2}, active_version="v2",
            column_state=self._column_state(), decrypt_failures=[],
        )
        self.assertTrue(any("不在当前密钥环" in r for r in reasons))

    def test_rejects_active_version(self):
        reasons = validate_key_retirement(
            version="v2", ring={"v1": K1, "v2": K2}, active_version="v2",
            column_state=self._column_state(), decrypt_failures=[],
        )
        self.assertTrue(any("激活版本" in r for r in reasons))

    def test_rejects_undecryptable_rows(self):
        reasons = validate_key_retirement(
            version="v1", ring={"v1": K1, "v2": K2}, active_version="v2",
            column_state=self._column_state(), decrypt_failures=["legal#3: boom"],
        )
        self.assertTrue(any("不可解密" in r for r in reasons))

    def test_rejects_remaining_legacy_ciphertext(self):
        reasons = validate_key_retirement(
            version="v1", ring={"v1": K1, "v2": K2}, active_version="v2",
            column_state=self._column_state({"v1": 2, "v2": 1}),
            decrypt_failures=[],
        )
        self.assertTrue(any("残留" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()
