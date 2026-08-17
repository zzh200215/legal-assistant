"""Task 层：Celery 信号处理器直调补测（task_runs 台账 + 上下文 + 指标）。

覆盖 app/tasks/signals.py：
- prerun：注册任务写台账（context_fn/business_key/attempt/queue）；
- success / failure / retry：终态台账 + 错误码（异常类型名，不落原始文本）；
- 未注册任务跳过；台账失败不阻断（异常吞掉）。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.tasks import signals


def _fake_task(name="connector_sync_task", retries=1, headers=None, queue=None):
    return SimpleNamespace(
        name=name,
        queue=queue,
        request=SimpleNamespace(headers=headers or {}, retries=retries),
    )


class CelerySignalHandlersTests(unittest.TestCase):
    def setUp(self):
        self._session_patch = patch("app.tasks.signals.SessionLocal", MagicMock())
        self._session_patch.start()

    def tearDown(self):
        self._session_patch.stop()

    def test_prerun_records_registered_task(self):
        task = _fake_task()
        with (
            patch("app.tasks.signals.task_run_service") as svc,
            patch("app.tasks.signals.context_from_headers", return_value={}),
            patch("app.tasks.signals.build_context") as build,
            patch("app.tasks.signals.set_context"),
        ):
            build.return_value = SimpleNamespace(trace_id="t1", request_id="r1", agent_run_id=None)
            signals._on_task_prerun(task_id="task-1", task=task, args=(1,), kwargs={})
        svc.start.assert_called_once()
        self.assertEqual(svc.start.call_args.kwargs["task_id"], "task-1")
        self.assertEqual(svc.start.call_args.kwargs["task_name"], "connector_sync_task")
        self.assertEqual(svc.start.call_args.kwargs["attempt"], 2)  # retries + 1
        self.assertEqual(svc.start.call_args.kwargs["business_key"], "connector:1")

    def test_prerun_skips_unregistered_task(self):
        task = _fake_task(name="some_unregistered_task")
        with (
            patch("app.tasks.signals.task_run_service") as svc,
            patch("app.tasks.signals.set_context"),
            patch("app.tasks.signals.context_from_headers", return_value={}),
            patch("app.tasks.signals.build_context"),
        ):
            signals._on_task_prerun(task_id="task-x", task=task, args=(), kwargs={})
        svc.start.assert_not_called()

    def test_prerun_tolerates_registry_failure(self):
        task = _fake_task()
        with (
            patch("app.tasks.signals.task_run_service") as svc,
            patch("app.tasks.signals.set_context"),
            patch("app.tasks.signals.context_from_headers", return_value={}),
            patch("app.tasks.signals.build_context"),
        ):
            svc.start.side_effect = RuntimeError("db down")
            signals._on_task_prerun(task_id="task-1", task=task, args=(1,), kwargs={})  # 不抛

    def test_success_marks_succeeded(self):
        task = _fake_task()
        with patch("app.tasks.signals.task_run_service") as svc:
            signals._on_task_success(task_id="task-1", task=task)
        svc.mark_succeeded.assert_called_once()
        self.assertEqual(svc.mark_succeeded.call_args.kwargs["task_id"], "task-1")

    def test_failure_marks_failed_with_type_name_only(self):
        task = _fake_task()
        with patch("app.tasks.signals.task_run_service") as svc:
            signals._on_task_failure(task_id="task-1", task=task, exception=ValueError("secret-key-leak"))
        svc.mark_failed.assert_called_once()
        self.assertEqual(svc.mark_failed.call_args.kwargs["error_code"], "ValueError")
        self.assertIsNone(svc.mark_failed.call_args.kwargs["error_message"])  # 不落原始文本

    def test_retry_marks_retrying_with_eta(self):
        task = _fake_task()
        with patch("app.tasks.signals.task_run_service") as svc:
            signals._on_task_retry(
                task_id="task-1", task=task, request=SimpleNamespace(retries=2, eta="2026-08-16T12:00:00Z"),
                einfo=SimpleNamespace(exception=TimeoutError("slow")),
            )
        svc.mark_retrying.assert_called_once()
        self.assertEqual(svc.mark_retrying.call_args.kwargs["attempt"], 3)
        self.assertEqual(svc.mark_retrying.call_args.kwargs["error_code"], "TimeoutError")
        self.assertEqual(svc.mark_retrying.call_args.kwargs["next_retry_at"], "2026-08-16T12:00:00Z")

    def test_error_fields_extraction(self):
        self.assertEqual(signals._error_fields(None), (None, None))
        code, message = signals._error_fields(RuntimeError("x"))
        self.assertEqual(code, "RuntimeError")
        self.assertIsNone(message)

    def test_postrun_resets_context(self):
        with patch("app.tasks.signals.reset_context") as reset:
            signals._on_task_postrun()
        reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
