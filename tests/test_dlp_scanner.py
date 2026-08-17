"""DLP 扫描器测试：allow / block / review_required / 附件强制审批 / fail closed / 未配置不伪造通过。"""
import unittest
from unittest.mock import patch

from app.core.config import get_settings
from app.services.notification.dlp_scanner import DlpScanner


class DlpScannerTests(unittest.TestCase):
    def setUp(self):
        self.scanner = DlpScanner()

    def test_allow_clean(self):
        result = self.scanner.scan(payloads=["普通主题", "普通正文"], action="block")
        self.assertEqual(result.decision, "allow")
        self.assertEqual(result.status, "clean")
        self.assertFalse(result.blocked)

    def test_block_high_risk_secret(self):
        result = self.scanner.scan(
            payloads=["请使用令牌 sk_abcdefghijklmnopqrstuvwxyz123456 访问接口"], action="block")
        self.assertEqual(result.decision, "block")
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.blocked)
        self.assertIn("api_token", result.masked_summary)
        self.assertNotIn("sk_abcdefghijklmnopqrstuvwxyz123456", result.masked_summary)

    def test_review_required_for_batch_recipients(self):
        result = self.scanner.scan(payloads=["无敏感内容"], action="block", recipient_count=8)
        self.assertEqual(result.decision, "review_required")
        self.assertEqual(result.status, "review_required")

    def test_review_required_for_attachment(self):
        result = self.scanner.scan(payloads=["附件待审"], action="block", has_attachment=True)
        self.assertEqual(result.decision, "review_required")

    def test_fail_closed_on_scanner_error(self):
        with patch("app.services.notification.dlp_scanner.data_protection_service.inspect", side_effect=RuntimeError("boom")):
            result = self.scanner.scan(payloads=["任何内容"], action="block")
        self.assertEqual(result.decision, "block")
        self.assertEqual(result.error_code, "DLP_SCAN_ERROR")
        self.assertTrue(result.blocked)

    def test_not_configured_does_not_fake_pass(self):
        with patch.object(get_settings(), "DLP_SCANNER_MODE", "disabled"):
            result = self.scanner.scan(payloads=["普通内容"], action="block")
        self.assertEqual(result.status, "not_configured")
        self.assertEqual(result.error_code, "DLP_NOT_CONFIGURED")
        self.assertTrue(result.blocked, "未配置扫描器默认 fail closed 阻断")

    def test_healthy_false_when_disabled(self):
        with patch.object(get_settings(), "DLP_SCANNER_MODE", "disabled"):
            self.assertFalse(self.scanner.healthy())
        self.assertTrue(self.scanner.healthy())


if __name__ == "__main__":
    unittest.main()
