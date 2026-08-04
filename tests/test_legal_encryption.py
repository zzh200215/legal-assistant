"""E-2a: legal-data encryption key rotation tests.

Cover the versioned key ring mechanics used by scripts/rotate_encryption_key.py:
- encrypt_text writes the active version prefix
- old-version ciphertext stays decryptable while the old key is still in the ring
  (dual-key transition window)
- removing the old key from the ring breaks legacy ciphertext (retire step)
- decrypt_text can address a specific version while it is still present
"""
import base64
import json
import unittest
from unittest.mock import patch

from app.core import encryption

K1 = base64.urlsafe_b64encode(b"1" * 32).decode("ascii")
K2 = base64.urlsafe_b64encode(b"2" * 32).decode("ascii")


def _settings(active_version="v1", keys=None, single_key=""):
    keys = keys or {"v1": K1}
    return type(
        "FakeSettings",
        (),
        {
            "LEGAL_DATA_ENCRYPTION_KEY": single_key,
            "LEGAL_DATA_ENCRYPTION_KEYS_JSON": json.dumps(keys),
            "LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION": active_version,
        },
    )()


class LegalEncryptionTests(unittest.TestCase):
    def test_aes_gcm_ciphertext_does_not_expose_plaintext(self):
        source = "张三的技术服务合同，联系电话 13800138000"
        ciphertext = encryption.encrypt_text(source)

        self.assertTrue(ciphertext.startswith("enc:v1:"))
        self.assertNotIn(source, ciphertext)
        self.assertEqual(encryption.decrypt_text(ciphertext), source)

    def test_legacy_plaintext_remains_readable_during_rollout(self):
        self.assertEqual(encryption.decrypt_text("历史明文记录"), "历史明文记录")


class LegalEncryptionRotationTests(unittest.TestCase):
    def test_encrypt_writes_active_version_prefix(self):
        with patch.object(
            encryption,
            "get_settings",
            return_value=_settings(active_version="v2", keys={"v1": K1, "v2": K2}),
        ):
            cipher = encryption.encrypt_text("机密内容")
        self.assertTrue(cipher.startswith("enc:v2:"))
        # After rotation the new active key must still round-trip.
        with patch.object(
            encryption,
            "get_settings",
            return_value=_settings(active_version="v2", keys={"v1": K1, "v2": K2}),
        ):
            self.assertEqual(encryption.decrypt_text(cipher), "机密内容")

    def test_old_version_ciphertext_readable_during_dual_key_transition(self):
        with patch.object(
            encryption,
            "get_settings",
            return_value=_settings(active_version="v1", keys={"v1": K1}),
        ):
            old_cipher = encryption.encrypt_text("旧版密文")
        self.assertTrue(old_cipher.startswith("enc:v1:"))

        # Rotation to v2 keeps v1 in the ring -> legacy rows still decryptable.
        with patch.object(
            encryption,
            "get_settings",
            return_value=_settings(active_version="v2", keys={"v1": K1, "v2": K2}),
        ):
            self.assertEqual(encryption.decrypt_text(old_cipher), "旧版密文")
            self.assertTrue(encryption.encrypt_text("新密文").startswith("enc:v2:"))

    def test_retiring_old_key_breaks_legacy_ciphertext(self):
        with patch.object(
            encryption,
            "get_settings",
            return_value=_settings(active_version="v1", keys={"v1": K1}),
        ):
            old_cipher = encryption.encrypt_text("轮换前内容")

        # After the old key is removed from the ring, legacy v1 rows must fail closed.
        with patch.object(
            encryption,
            "get_settings",
            return_value=_settings(active_version="v2", keys={"v2": K2}),
        ):
            with self.assertRaises(ValueError):
                encryption.decrypt_text(old_cipher)

    def test_explicit_version_resolution_until_retired(self):
        with patch.object(
            encryption,
            "get_settings",
            return_value=_settings(active_version="v1", keys={"v1": K1, "v2": K2}),
        ):
            version, key = encryption._key("v1")
            self.assertEqual(version, "v1")
            self.assertEqual(len(key), 32)

    def test_reencrypt_rewrites_to_active_version(self):
        """Mirror rotate_encryption_key.reencrypt_all: old rows get rewritten to v2."""
        with patch.object(
            encryption,
            "get_settings",
            return_value=_settings(active_version="v1", keys={"v1": K1}),
        ):
            old_cipher = encryption.encrypt_text("待重加密内容")

        with patch.object(
            encryption,
            "get_settings",
            return_value=_settings(active_version="v2", keys={"v1": K1, "v2": K2}),
        ):
            plain = encryption.decrypt_text(old_cipher)
            rewritten = encryption.encrypt_text(plain)
            self.assertTrue(rewritten.startswith("enc:v2:"))
            self.assertEqual(encryption.decrypt_text(rewritten), "待重加密内容")


if __name__ == "__main__":
    unittest.main()
