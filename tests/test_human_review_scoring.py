"""三层评审第三层（人工抽检）工具的回归测试。

覆盖两个脚本的**判定与统计口径**，因为这些数字会直接写进交付文档：
`scripts/score_human_review.py`（一致率、Wilson 区间、验收阈值）与
`scripts/human_sheet_digest.py`（抽检表解析、结论回填、非法值拒绝）。

设计原则：用最小合成表驱动，不依赖 eval/outputs 下的真实产物（那些按约定不入库）。
风格与仓库既有测试一致：unittest.TestCase（pytest 会收集 unittest 子类）。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(module_name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


scoring = _load("score_human_review_mod", "scripts/score_human_review.py")
digest = _load("human_sheet_digest_mod", "scripts/human_sheet_digest.py")


SHEET = """# 合成抽检表

## 一、分歧项

### 1. 题目甲｜质量·分歧项
<!-- meta：id=cross-001 kind=quality item=divergent glm=review qwen-plus=review -->

- 类型：质量｜本条目：分歧项｜裁判判定：review（两位裁判判罚一致）
- 问题：甲？
- 系统答案：甲答案。
- 人工结论：
- 备注：

- **glm-5.2**：review｜理由：略
- **qwen-plus**：review｜理由：略

### 2. 题目乙｜忠实度·一致项
<!-- meta：id=cross-002 kind=faithfulness item=agree glm=忠实 qwen-plus=忠实 -->

- 类型：忠实度｜本条目：一致项｜裁判判定：忠实（两位裁判一致）
- 问题：乙？
- 系统答案：乙答案。
- 人工结论：
- 备注：

- **glm-5.2**：忠实（faithful=True）｜理由：略
- **qwen-plus**：忠实（faithful=True）｜理由：略

### 3. 题目丙｜质量·一致项
<!-- meta：id=cross-003 kind=quality item=agree glm=pass qwen-plus=pass -->

- 类型：质量｜本条目：一致项｜裁判判定：pass（两位裁判一致）
- 问题：丙？
- 系统答案：丙答案。
- 人工结论：
- 备注：

- **glm-5.2**：pass｜理由：略
- **qwen-plus**：pass｜理由：略
"""


def _fill(text: str, marker: str, verdict: str, note: str = "") -> str:
    """把某条裁判行的前一个空结论填上（marker 用于定位是哪一条）。"""
    target = f"- 人工结论：\n- 备注：\n\n- **glm-5.2**：{marker}"
    replacement = f"- 人工结论：{verdict}\n- 备注：{note}\n\n- **glm-5.2**：{marker}"
    assert target in text, f"合成表里找不到定位标记：{marker}"
    return text.replace(target, replacement, 1)


class WilsonIntervalTests(unittest.TestCase):
    def test_known_value_matches_hand_calculation(self):
        low, high = scoring.wilson_interval(18, 24)
        self.assertAlmostEqual(low, 0.551, places=3)
        self.assertAlmostEqual(high, 0.880, places=3)

    def test_zero_denominator_returns_none(self):
        self.assertIsNone(scoring.wilson_interval(0, 0))

    def test_bounds_stay_inside_unit_interval(self):
        low, high = scoring.wilson_interval(25, 25)
        self.assertLessEqual(0.0, low)
        self.assertLessEqual(low, high)
        self.assertLessEqual(high, 1.0)
        self.assertGreater(low, 0.8)

    def test_interval_narrows_as_sample_grows(self):
        small = scoring.wilson_interval(16, 20)
        large = scoring.wilson_interval(160, 200)
        self.assertLess(large[1] - large[0], small[1] - small[0])


class ParseSheetTests(unittest.TestCase):
    def test_parses_entries_meta_and_verdicts(self):
        entries = scoring.parse_sheet(SHEET)
        self.assertEqual([entry["id"] for entry in entries], ["cross-001", "cross-002", "cross-003"])
        self.assertEqual([entry["kind"] for entry in entries], ["quality", "faithfulness", "quality"])
        self.assertTrue(all(entry["human"] is None for entry in entries))
        # 裁判侧归一成二值：review → fail（"需人工复核"不算 pass），忠实 → pass
        self.assertEqual(entries[0]["sides"], {"glm": "fail", "qwen-plus": "fail"})
        self.assertEqual(entries[1]["sides"], {"glm": "pass", "qwen-plus": "pass"})

    def test_review_verdict_counts_as_not_pass(self):
        self.assertEqual(scoring.normalize_side("review"), "fail")
        self.assertEqual(scoring.normalize_side("pass"), "pass")
        self.assertEqual(scoring.normalize_side("忠实"), "pass")
        self.assertEqual(scoring.normalize_side("不忠实"), "fail")
        # 人工判「正确」（=可 pass）遇上裁判 review → 记为不同向
        result = scoring.build_result(scoring.parse_sheet(_fill(SHEET, "review", "正确")))
        self.assertEqual(result["agreement"]["glm"]["numerator"], 0)
        self.assertEqual(result["agreement"]["glm"]["denominator"], 1)

    def test_normalizes_human_verdicts(self):
        text = _fill(SHEET, "忠实", "无法判断")
        text = _fill(text, "pass", "正确", "没问题")
        entries = scoring.parse_sheet(text)
        self.assertEqual(entries[1]["human"], "unknown")
        self.assertEqual(entries[2]["human"], "correct")
        self.assertEqual(entries[2]["note_raw"], "没问题")


class AgreementTests(unittest.TestCase):
    def test_agreement_counts_only_entries_with_both_sides(self):
        text = _fill(SHEET, "review", "正确")
        text = _fill(text, "忠实", "错误")
        text = _fill(text, "pass", "无法判断")
        result = scoring.build_result(scoring.parse_sheet(text))
        self.assertEqual(result["filled"], 2)
        self.assertEqual(result["unknown"], 1)
        glm = result["agreement"]["glm"]
        # cross-001：人工「正确」vs 裁判 review → 不同向；cross-002：人工「错误」vs 忠实 → 不同向
        self.assertEqual(glm["denominator"], 2)
        self.assertEqual(glm["numerator"], 0)
        self.assertEqual(set(glm["disagreed_entries"]), {"cross-001", "cross-002"})
        self.assertIsNotNone(glm["wilson_95"])

    def test_agreement_direction_uses_pass_semantics(self):
        # 质量条目：人工「正确」与裁判 pass 同向
        text = _fill(SHEET, "pass", "正确")
        result = scoring.build_result(scoring.parse_sheet(text))
        self.assertEqual(result["agreement"]["glm"]["numerator"], 1)
        self.assertEqual(result["agreement"]["glm"]["denominator"], 1)

    def test_acceptance_requires_nonzero_denominator(self):
        result = scoring.build_result(scoring.parse_sheet(SHEET))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            scoring.print_acceptance(result, 0.8)
        out = buffer.getvalue()
        self.assertIn("未达成（分母 0", out)
        self.assertIn("尚未达成", out)

    def test_acceptance_flags_small_sample(self):
        summary = {
            "side": "glm",
            "numerator": 20,
            "denominator": 25,
            "rate": 0.8,
            "wilson_95": scoring.wilson_interval(20, 25),
            "disagreed_entries": [],
        }
        result = {"sides": ["glm"], "agreement": {"glm": summary}}
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            scoring.print_acceptance(result, 0.8)
        out = buffer.getvalue()
        self.assertIn("小样本：95% CI 下界低于阈值", out)
        self.assertIn("达成", out)


class DigestFillTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.sheet = self.tmp_path / "sheet.md"
        self.sheet.write_text(SHEET, encoding="utf-8")

    def test_split_sheet_reads_entries(self):
        head, entries = digest.split_sheet(self.sheet.read_text(encoding="utf-8"))
        self.assertIn("# 合成抽检表", head)
        self.assertEqual([entry.id for entry in entries], ["cross-001", "cross-002", "cross-003"])

    def test_fill_writes_verdict_and_note(self):
        head, entries = digest.split_sheet(self.sheet.read_text(encoding="utf-8"))
        blocks = digest.apply_fills(entries, {"cross-002": "错误"}, {"cross-002": "与片段不一致"})
        updated = head + "\n" + "\n\n".join(blocks) + "\n"
        self.assertIn("- 人工结论：错误", updated)
        self.assertIn("- 备注：与片段不一致", updated)
        self.assertEqual(updated.count("- 人工结论："), 3)
        self.assertEqual(updated.count("- 人工结论：\n"), 2)

    def test_invalid_verdict_is_rejected(self):
        out = self.tmp_path / "filled.md"
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = digest.main(["--sheet", str(self.sheet), "--fill", "cross-001=对", "--out", str(out)])
        self.assertEqual(code, 2)
        self.assertIn("结论只能是", buffer.getvalue())
        self.assertFalse(out.exists())

    def test_unknown_id_is_rejected(self):
        out = self.tmp_path / "filled.md"
        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            code = digest.main(["--sheet", str(self.sheet), "--fill", "cross-999=正确", "--out", str(out)])
        self.assertEqual(code, 2)
        self.assertIn("表里没有这个 id", buffer.getvalue())

    def test_digest_prints_requested_range(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = digest.main(["--sheet", str(self.sheet), "--digest", "2", "3"])
        out = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("[2] cross-002", out)
        self.assertIn("[3] cross-003", out)
        self.assertNotIn("[1] cross-001", out)


if __name__ == "__main__":
    unittest.main()
