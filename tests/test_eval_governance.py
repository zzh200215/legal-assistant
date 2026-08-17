"""评估集治理（阶段 4）测试：PII 脱敏 / 分层采样 / seed 可复现 / 延迟分位。

覆盖：
- eval/redact.py：redact_pii 规则（姓名/手机/证件/金额/邮箱/案号）与 detect_pii 校验；
- eval/stratified_sampler.py：分层键与成比例采样（固定 seed 可复现）；
- eval/common.py：set_eval_seed 固定随机种子；
- eval/run_eval.py：_percentile 延迟分位口径。
"""

import random
import unittest

from eval.common import set_eval_seed
from eval.redact import detect_pii, redact_pii
from eval.run_eval import _percentile
from eval.stratified_sampler import strata_counts, strata_of, stratified_sample


class RedactPiiTests(unittest.TestCase):
    def test_redacts_common_pii(self):
        text = "当事人张三电话13800138000，身份证110101199001011234，金额¥12,345.60，邮箱a@b.com"
        out = redact_pii(text)
        self.assertNotIn("13800138000", out)
        self.assertNotIn("110101199001011234", out)
        self.assertNotIn("a@b.com", out)
        self.assertIn("【当事人】", out)
        self.assertIn("【联系电话】", out)
        self.assertIn("【证件号】", out)
        self.assertIn("【金额】", out)
        self.assertIn("【邮箱】", out)

    def test_redacts_case_number_and_firm(self):
        text = "（2026）京01民初123号 张三律师事务所"
        out = redact_pii(text)
        self.assertIn("【案号】", out)
        self.assertIn("【律师事务所】", out)

    def test_redact_keeps_normal_text(self):
        text = "本判决依据《中华人民共和国民法典》作出。"
        self.assertEqual(redact_pii(text), text)

    def test_detect_pii(self):
        hits = detect_pii("手机 13900001111")
        self.assertIn("phone", hits)
        self.assertEqual(detect_pii("无敏感内容，仅法律条文"), [])
        self.assertEqual(detect_pii(""), [])

    def test_redacted_text_has_no_residual(self):
        dirty = "王五 13800138000 110101199001011234 ¥1000 mail@test.com （2025）粤01民初9号"
        clean = redact_pii(dirty)
        self.assertEqual(detect_pii(clean), [])


class StratifiedSamplerTests(unittest.TestCase):
    def _cases(self):
        return [
            {"category": "劳动", "question": f"q{i}", "should_refuse": i % 5 == 0,
             "expected_answer_keywords": ["a", "b", "c"] if i % 2 else ["a"]}
            for i in range(20)
        ]

    def test_strata_of_refusal_is_high(self):
        case = {"category": "劳动", "should_refuse": True, "expected_answer_keywords": []}
        self.assertEqual(strata_of(case), ("劳动", "refuse", "high"))

    def test_strata_difficulty_by_keywords(self):
        low = {"category": "x", "should_refuse": False, "expected_answer_keywords": []}
        mid = {"category": "x", "should_refuse": False, "expected_answer_keywords": ["a"]}
        high = {"category": "x", "should_refuse": False, "expected_answer_keywords": ["a", "b", "c"]}
        self.assertEqual(strata_of(low)[2], "low")
        self.assertEqual(strata_of(mid)[2], "medium")
        self.assertEqual(strata_of(high)[2], "high")

    def test_stratified_sample_reproducible(self):
        cases = self._cases()
        a, _ = stratified_sample(cases, 10, seed=7)
        b, _ = stratified_sample(cases, 10, seed=7)
        c, _ = stratified_sample(cases, 10, seed=8)
        self.assertEqual([x["question"] for x in a], [x["question"] for x in b])
        self.assertNotEqual([x["question"] for x in a], [x["question"] for x in c])

    def test_stratified_sample_size_and_strata(self):
        cases = self._cases()
        sampled, stats = stratified_sample(cases, 10, seed=42)
        self.assertEqual(len(sampled), 10)
        # 分层键覆盖正常
        before = strata_counts(cases)
        after = strata_counts(sampled)
        self.assertEqual(set(after.keys()) <= set(before.keys()), True)

    def test_empty_and_zero(self):
        self.assertEqual(stratified_sample([], 5, seed=1), ([], {"strata": {}, "sampled": 0, "total": 0}))


class SeedTests(unittest.TestCase):
    def test_set_eval_seed_reproducible_sequence(self):
        set_eval_seed(123)
        a = [random.randint(0, 1000) for _ in range(5)]
        set_eval_seed(123)
        b = [random.randint(0, 1000) for _ in range(5)]
        self.assertEqual(a, b)


class PercentileTests(unittest.TestCase):
    def test_percentile_linear_interpolation(self):
        self.assertEqual(_percentile([], 95), 0.0)
        self.assertEqual(_percentile([10], 50), 10.0)
        self.assertEqual(_percentile([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(_percentile([1, 2, 3, 4, 5], 95), 4.8)


if __name__ == "__main__":
    unittest.main()
