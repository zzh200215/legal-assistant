import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.operational_alert_service import operational_alert_service


class OperationalAlertServiceTests(unittest.TestCase):
    def test_disabled_webhook_does_not_send(self):
        with patch("app.services.operational_alert_service.get_settings", return_value=SimpleNamespace(ALERT_WEBHOOK_URL="")):
            result = operational_alert_service.dispatch(db=Mock())
        self.assertEqual(result, {"status": "disabled", "sent_count": 0})

    def test_high_severity_alert_is_redacted_and_deduplicated(self):
        settings = SimpleNamespace(
            ALERT_WEBHOOK_URL="https://robot.example.test/hook",
            ALERT_WEBHOOK_MIN_SEVERITY="high",
            ALERT_WEBHOOK_TIMEOUT_SECONDS=5,
            REDIS_URL="redis://example.test/0",
        )
        cache = Mock()
        cache.get.return_value = None
        response = Mock()
        response.raise_for_status.return_value = None
        alerts = [{
            "source": "mailbox", "source_label": "邮箱同步", "category": "mailbox_sync_error",
            "severity": "high", "target_type": "connector_sync_job", "target_id": 7,
            "message": "password=must-not-leave-the-system", "created_at": datetime.utcnow(),
        }]
        with patch("app.services.operational_alert_service.get_settings", return_value=settings), \
             patch("app.services.operational_alert_service.analytics_service.list_alerts", return_value=alerts), \
             patch("app.services.operational_alert_service.analytics_service.get_llm_routing_health", return_value={"warnings": []}), \
             patch("app.services.operational_alert_service.redis.from_url", return_value=cache), \
             patch("app.services.operational_alert_service.httpx.post", return_value=response) as post, \
             patch("app.services.operational_alert_service.oplog_service.log"):
            result = operational_alert_service.dispatch(db=Mock())
        self.assertEqual(result, {"status": "sent", "sent_count": 1})
        content = post.call_args.kwargs["json"]["markdown"]["content"]
        self.assertIn("邮箱同步", content)
        self.assertNotIn("must-not-leave-the-system", content)
        self.assertEqual(cache.set.call_count, 1)

    def test_routing_health_degradation_is_sent_as_redacted_alert(self):
        settings = SimpleNamespace(
            ALERT_WEBHOOK_URL="https://robot.example.test/hook",
            ALERT_WEBHOOK_MIN_SEVERITY="high",
            ALERT_WEBHOOK_TIMEOUT_SECONDS=5,
            REDIS_URL="redis://example.test/0",
        )
        cache = Mock()
        cache.get.return_value = None
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("app.services.operational_alert_service.get_settings", return_value=settings), \
             patch("app.services.operational_alert_service.analytics_service.list_alerts", return_value=[]), \
             patch("app.services.operational_alert_service.analytics_service.get_llm_routing_health", return_value={"warnings": ["primary_failure_rate_high"]}), \
             patch("app.services.operational_alert_service.redis.from_url", return_value=cache), \
             patch("app.services.operational_alert_service.httpx.post", return_value=response) as post, \
             patch("app.services.operational_alert_service.oplog_service.log"):
            result = operational_alert_service.dispatch(db=Mock())

        self.assertEqual(result, {"status": "sent", "sent_count": 1})
        content = post.call_args.kwargs["json"]["markdown"]["content"]
        self.assertIn("模型路由", content)
        self.assertIn("model_routing_degraded", content)


if __name__ == "__main__":
    unittest.main()
