import unittest

from app.services.org.data_protection_service import data_protection_service


class DataProtectionServiceTests(unittest.TestCase):
    def test_detects_and_redacts_personal_and_secret_values_without_returning_raw_values(self):
        source = "联系张三 13812345678，身份证 11010519491231002X，令牌 sk_abcdefghijklmnopqrstuvwxyz123456"
        result = data_protection_service.redact(source)
        codes = {item["code"] for item in result["findings"]}
        self.assertTrue({"mobile_phone", "cn_id_card", "api_token"}.issubset(codes))
        self.assertNotIn("13812345678", result["text"])
        self.assertNotIn("11010519491231002X", result["text"])
        self.assertNotIn("sk_abcdefghijklmnopqrstuvwxyz123456", result["text"])
        self.assertTrue(data_protection_service.should_block(result, action="block"))
        self.assertFalse(data_protection_service.should_block(result, action="warn"))
        summary = data_protection_service.audit_summary(result)
        self.assertIn("api_token=1", summary)
        self.assertNotIn("13812345678", summary)


if __name__ == "__main__":
    unittest.main()
