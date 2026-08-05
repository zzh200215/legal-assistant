"""M-3 A/B 判定脚本单元测试（决策逻辑离线可测，不依赖库）。"""
import unittest

from scripts.evaluate_ab_conversion import decide, _d7_active
from datetime import datetime, timedelta


class ABDecideTests(unittest.TestCase):
    def test_sample_insufficient_below_threshold(self):
        data = {
            "groups": {
                "A": {"registered": 10, "intent": 1, "conversion_rate": 0.1, "d7_rate": 0.5},
                "B": {"registered": 10, "intent": 2, "conversion_rate": 0.2, "d7_rate": 0.6},
            }
        }
        d = decide(data, min_sample=30)
        self.assertEqual(d["conclusion"], "sample_insufficient")

    def test_promote_b_when_uplift_significant_and_retention_ok(self):
        # B 转化 40/100，A 转化 25/100 → 提升 60%，构造显著
        data = {
            "groups": {
                "A": {"registered": 100, "intent": 25, "conversion_rate": 0.25, "d7_rate": 0.5},
                "B": {"registered": 100, "intent": 40, "conversion_rate": 0.40, "d7_rate": 0.55},
            }
        }
        d = decide(data, min_sample=30)
        self.assertEqual(d["conclusion"], "promote_b")
        self.assertTrue(d["significant"])
        self.assertGreaterEqual(d["b_uplift_vs_a"], 0.30)

    def test_keep_status_quo_when_no_significant_diff(self):
        data = {
            "groups": {
                "A": {"registered": 100, "intent": 30, "conversion_rate": 0.30, "d7_rate": 0.5},
                "B": {"registered": 100, "intent": 32, "conversion_rate": 0.32, "d7_rate": 0.52},
            }
        }
        d = decide(data, min_sample=30)
        self.assertEqual(d["conclusion"], "keep_status_quo")

    def test_mixed_when_uplift_significant_but_retention_drops(self):
        data = {
            "groups": {
                "A": {"registered": 100, "intent": 25, "conversion_rate": 0.25, "d7_rate": 0.60},
                "B": {"registered": 100, "intent": 45, "conversion_rate": 0.45, "d7_rate": 0.40},
            }
        }
        d = decide(data, min_sample=30)
        self.assertEqual(d["conclusion"], "mixed")

    def test_d7_active_window(self):
        created = datetime(2026, 8, 1, 9, 0, 0)
        lo = created + timedelta(days=7)
        self.assertTrue(_d7_active(created.isoformat(), [lo + timedelta(hours=1)]))
        self.assertFalse(_d7_active(created.isoformat(), [lo - timedelta(hours=1)]))
        self.assertFalse(_d7_active(created.isoformat(), [lo + timedelta(days=7)]))


if __name__ == "__main__":
    unittest.main()
