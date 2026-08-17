"""Service 层纯逻辑：task_service._parse_due_date 边界测试。

覆盖 app/services/jobs/task_service.py::TaskService._parse_due_date：
日期字符串规范化——10 位日期补午夜、ISO/空格分隔 datetime 保留、非法与空值 → None。
"""

import unittest

from app.services.jobs.task_service import task_service


class ParseDueDateTests(unittest.TestCase):
    def test_empty_values_return_none(self):
        self.assertIsNone(task_service._parse_due_date(None))
        self.assertIsNone(task_service._parse_due_date(""))
        self.assertIsNone(task_service._parse_due_date("   "))

    def test_date_only_becomes_midnight(self):
        dt = task_service._parse_due_date("2026-08-01")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.isoformat(), "2026-08-01T00:00:00")

    def test_full_iso_datetime_preserved(self):
        dt = task_service._parse_due_date("2026-08-01T09:30:00")
        self.assertEqual(dt.isoformat(), "2026-08-01T09:30:00")

    def test_space_separated_datetime_accepted(self):
        dt = task_service._parse_due_date("2026-08-01 09:30:00")
        self.assertEqual(dt.isoformat(), "2026-08-01T09:30:00")

    def test_invalid_strings_return_none(self):
        for bad in ("not-a-date", "2026-13-01", "2026-08-32", "12345", "2026-08-01T25:00:00"):
            self.assertIsNone(task_service._parse_due_date(bad), bad)


if __name__ == "__main__":
    unittest.main()
