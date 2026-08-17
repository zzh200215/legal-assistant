"""队列路由与超时测试：所有注册任务显式归属一队列、无遗漏、旧任务名保留。"""
import unittest
from unittest.mock import patch

from app.core.celery_app import _QUEUE_LIMITS, _routes, celery_app
from app.core.config import get_settings


def _registered_task_names() -> set[str]:
    return {name for name in celery_app.tasks if name.startswith("app.tasks") or name in {
        "parse_document", "document_chunk", "document_index", "document_export",
        "recover_stale_document_jobs", "summarize_document", "analyze_document",
        "dispatch_operational_alerts", "run_database_archive", "check_legal_deadline_reminders",
        "scan_overdue_invoices", "scan_expired_portal_links", "scan_expired_subscriptions",
        "scan_contract_expiry_alerts", "dispatch_notification_events",
        "retry_failed_webhook_deliveries", "process_open_contract_review",
        "recover_queued_open_contract_reviews",
        "parse_contract_versions", "check_legal_approval_timeouts", "confirm_account_deletions",
        "create_pilot_backup", "dispatch_feishu_reminders", "connector_sync_task",
        "recover_stale_connector_syncs",
    }}


class TaskRoutingTests(unittest.TestCase):
    def test_document_export_task_fails_explicitly_until_implemented(self):
        from app.tasks import document_export_task

        with patch("app.tasks.document_tasks.log_async_task_event"):
            with self.assertRaises(NotImplementedError):
                document_export_task.run(document_id=1, export_type="archive", user_id=1)

    def test_all_registered_tasks_have_explicit_route(self):
        """验收 #1：注册的业务任务全部显式归属一队列，无遗漏落入默认队列。"""
        routes = _routes()
        registered = _registered_task_names()
        missing = sorted(registered - set(routes))
        self.assertEqual(missing, [], f"未路由任务: {missing}")
        # 每个路由都有 queue + 超时
        for name, route in routes.items():
            self.assertIn("queue", route, name)
            self.assertIn("time_limit", route, name)
            self.assertIn("soft_time_limit", route, name)
            self.assertIn(route["queue"], _QUEUE_LIMITS, name)

    def test_no_task_routes_to_default_celery_queue(self):
        routes = _routes()
        for name, route in routes.items():
            self.assertNotEqual(route["queue"], "celery", name)

    def test_per_queue_soft_below_hard_and_in_bounds(self):
        """验收 #2：每队列 soft_time_limit < time_limit，值在界内。"""
        for queue, (hard, soft) in _QUEUE_LIMITS.items():
            self.assertLess(soft, hard, f"{queue} soft 必须小于 hard")
            self.assertGreater(hard, 0)
            self.assertGreater(soft, 0)

    def test_default_queue_is_consumed_queue(self):
        """task_default_queue 必须是已消费队列（connector），杜绝无意进默认 celery。"""
        self.assertEqual(get_settings().TASK_DEFAULT_QUEUE, "connector")
        self.assertEqual(celery_app.conf.task_default_queue, "connector")

    def test_old_task_names_preserved(self):
        """验收 #13：旧任务名保留，.delay() 调用方式不变。"""
        routes = _routes()
        for old_name in (
            "parse_document", "document_chunk", "document_index", "summarize_document",
            "analyze_document", "dispatch_operational_alerts", "check_legal_deadline_reminders",
            "scan_overdue_invoices", "dispatch_notification_events",
            "retry_failed_webhook_deliveries", "process_open_contract_review",
            "create_pilot_backup", "dispatch_feishu_reminders",
        ):
            self.assertIn(old_name, routes, f"旧任务名 {old_name} 必须保留路由")

    def test_llm_and_document_isolated_on_separate_queues(self):
        """LLM 网络绑定任务与 CPU 重解析任务分属不同队列（隔离不阻塞）。"""
        routes = _routes()
        self.assertEqual(routes["parse_document"]["queue"], "document")
        self.assertEqual(routes["document_index"]["queue"], "document")
        self.assertEqual(routes["summarize_document"]["queue"], "llm")
        self.assertEqual(routes["analyze_document"]["queue"], "llm")
        self.assertNotEqual(routes["parse_document"]["queue"], routes["summarize_document"]["queue"])


if __name__ == "__main__":
    unittest.main()
