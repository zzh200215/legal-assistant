"""Task 层：task_retry.retry_task 重试上限 / 指数退避 / 文档进度语义测试。

覆盖 app/tasks/task_retry.py：
- 未达上限：raise self.retry(countdown=backoff_base*(retries+1))，记录 retrying 事件；
- 达上限：抛原始异常并记录 failed 事件（+文档任务 mark_failed）；
- 退避/上限可被调用方覆盖；
- 文档路径用 session_factory/document_jobs 注入，**不触碰真实 DB**（红线 2）。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.tasks import task_retry


def _fake_task(retries: int = 0) -> SimpleNamespace:
    task = SimpleNamespace(request=SimpleNamespace(id="task-r1", retries=retries))
    task.retry = MagicMock(side_effect=RuntimeError("self.retry called"))
    return task


def _doc_mocks():
    session = MagicMock()
    return {
        "session": session,
        "kwargs": {
            "session_factory": MagicMock(return_value=session),
            "document_jobs": MagicMock(),
        },
    }


class RetryTaskTests(unittest.TestCase):
    def test_below_limit_raises_self_retry_with_backoff(self):
        task = _fake_task(retries=1)
        log = MagicMock()
        mocks = _doc_mocks()
        with patch.object(task_retry, "get_settings") as settings:
            settings.return_value.DOCUMENT_TASK_MAX_RETRIES = 5
            settings.return_value.DOCUMENT_TASK_BACKOFF_BASE_SECONDS = 10
            with self.assertRaises(RuntimeError):
                task_retry.retry_task(
                    task,
                    ValueError("boom"),
                    user_id=1, target_type="document", target_id=2, action_prefix="document_parse",
                    log_event=log, **mocks["kwargs"],
                )
        task.retry.assert_called_once()
        countdown = task.retry.call_args.kwargs["countdown"]
        self.assertEqual(countdown, 20)  # 10 * (1+1)
        self.assertEqual(task.retry.call_args.kwargs["max_retries"], 5)
        log.assert_called_once()
        self.assertEqual(log.call_args.kwargs["action"], "document_parse_retrying")

    def test_custom_max_retries_and_backoff_override(self):
        task = _fake_task(retries=0)
        mocks = _doc_mocks()
        with self.assertRaises(RuntimeError):
            task_retry.retry_task(
                task,
                ValueError("boom"),
                user_id=1, target_type="document", target_id=2, action_prefix="x",
                max_retries=3, backoff_base=5,
                log_event=MagicMock(), **mocks["kwargs"],
            )
        self.assertEqual(task.retry.call_args.kwargs["countdown"], 5)
        self.assertEqual(task.retry.call_args.kwargs["max_retries"], 3)

    def test_at_limit_raises_original_and_logs_failed(self):
        task = _fake_task(retries=5)
        log = MagicMock()
        mocks = _doc_mocks()
        with patch.object(task_retry, "get_settings") as settings:
            settings.return_value.DOCUMENT_TASK_MAX_RETRIES = 5
            settings.return_value.DOCUMENT_TASK_BACKOFF_BASE_SECONDS = 10
            with self.assertRaises(ValueError):
                task_retry.retry_task(
                    task,
                    ValueError("boom"),
                    user_id=1, target_type="document", target_id=2, action_prefix="document_parse",
                    log_event=log, **mocks["kwargs"],
                )
        task.retry.assert_not_called()
        log.assert_called_once()
        self.assertEqual(log.call_args.kwargs["action"], "document_parse_failed")

    def test_document_path_updates_progress_before_retry(self):
        task = _fake_task(retries=1)
        mocks = _doc_mocks()
        with self.assertRaises(RuntimeError):
            task_retry.retry_task(
                task,
                ValueError("boom"),
                user_id=1, target_type="document", target_id=2, action_prefix="document_parse",
                log_event=MagicMock(), **mocks["kwargs"],
            )
        mocks["kwargs"]["document_jobs"].update_progress.assert_called_once()
        self.assertEqual(mocks["kwargs"]["document_jobs"].update_progress.call_args.kwargs["retry_count"], 2)
        mocks["session"].close.assert_called_once()

    def test_document_path_marks_failed_at_limit(self):
        task = _fake_task(retries=5)
        mocks = _doc_mocks()
        with self.assertRaises(ValueError):
            task_retry.retry_task(
                task,
                ValueError("boom"),
                user_id=1, target_type="document", target_id=2, action_prefix="document_parse",
                log_event=MagicMock(), max_retries=5, **mocks["kwargs"],
            )
        mocks["kwargs"]["document_jobs"].mark_failed.assert_called_once()
        # 文档分支无条件先 update_progress（开 1 会话）+ mark_failed（再开 1 会话）
        self.assertEqual(mocks["session"].close.call_count, 2)

    def test_non_document_target_skips_db(self):
        task = _fake_task(retries=1)
        session_factory = MagicMock()
        with self.assertRaises(RuntimeError):
            task_retry.retry_task(
                task,
                ValueError("boom"),
                user_id=1, target_type="notification", target_id=2, action_prefix="notify",
                log_event=MagicMock(), session_factory=session_factory,
            )
        session_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
