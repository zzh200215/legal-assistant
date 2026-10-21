"""合同审查第三层（独立裁决）的文件契约与评分注入逻辑。

裁决文件是跟踪的交付物（`eval/contract_review_adjudication.json`），抽检表是
`.gitignore` 内的运行产物，所以这里只用合成表验证解析/注入，不去读 `eval/outputs/`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.score_human_review import (
    apply_verdicts,
    build_result,
    load_verdicts,
    parse_sheet,
)

ROOT = Path(__file__).resolve().parents[1]
ADJUDICATION = ROOT / "eval" / "contract_review_adjudication.json"

SYNTHETIC_SHEET = """# 抽检
### 1. a-grounding｜有据性·示例
<!-- meta：id=a-grounding kind=faithfulness judge=忠实 judge2=不忠实 -->

- 人工结论：

### 2. a-quality｜质量·示例
<!-- meta：id=a-quality kind=quality judge=pass judge2=review -->

- 人工结论：错误
"""


def test_adjudication_file_is_well_formed():
    payload = json.loads(ADJUDICATION.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    adjudicator = payload["adjudicator"]
    assert adjudicator["kind"] == "ai"  # 本项目里不允许把 AI 复核算成人工
    assert adjudicator["rubric"]["faithfulness"] and adjudicator["rubric"]["quality"]

    entries = payload["entries"]
    assert len(entries) == 14
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))

    kinds = [entry["kind"] for entry in entries]
    assert kinds.count("faithfulness") == 7
    assert kinds.count("quality") == 7

    for entry in entries:
        assert entry["verdict"] in {"correct", "wrong", "unknown"}, entry["id"]
        assert entry["rationale"].strip(), f"{entry['id']} 缺少理由"
        assert entry["evidence"].strip(), f"{entry['id']} 缺少证据"
        assert entry["id"].startswith("contract-"), entry["id"]


def test_adjudication_ids_follow_sheet_naming():
    payload = json.loads(ADJUDICATION.read_text(encoding="utf-8"))
    expected = {
        f"contract-{index}-{kind}" for index in range(1, 8) for kind in ("grounding", "quality")
    }
    assert {entry["id"] for entry in payload["entries"]} == expected


def test_load_verdicts_rejects_broken_shape(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"entries": [{"verdict": "correct"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="id"):
        load_verdicts(bad)


def test_apply_verdicts_only_fills_blanks_and_reports_unknown_ids():
    entries = parse_sheet(SYNTHETIC_SHEET)
    verdicts = {
        "a-grounding": {"verdict": "correct", "rationale": "原文可核"},
        "a-quality": {"verdict": "correct"},  # 表里已填「错误」，人工优先
        "not-in-sheet": {"verdict": "wrong"},
    }
    stats = apply_verdicts(entries, verdicts)

    assert stats["applied"] == ["a-grounding"]
    assert stats["skipped_human_filled"] == ["a-quality"]
    assert stats["unknown_ids"] == ["not-in-sheet"]

    by_id = {entry["id"]: entry for entry in entries}
    assert by_id["a-grounding"]["human"] == "correct"
    assert by_id["a-grounding"]["human_source"] == "adjudication"
    assert by_id["a-quality"]["human"] == "wrong"  # 人工结论没有被覆盖


def test_apply_verdicts_maps_judge_labels():
    entries = parse_sheet(SYNTHETIC_SHEET)
    verdicts = {
        "a-grounding": {"verdict": "pass"},
        "a-quality": {"verdict": "review"},
    }
    stats = apply_verdicts(entries, verdicts)
    by_id = {entry["id"]: entry for entry in entries}
    assert by_id["a-grounding"]["human"] == "correct"  # pass → 可接受
    assert by_id["a-quality"]["human"] == "wrong"  # review → 不可接受
    assert stats["applied"] == ["a-grounding"]


def test_unmapped_verdict_is_skipped():
    entries = parse_sheet(SYNTHETIC_SHEET)
    stats = apply_verdicts(entries, {"a-grounding": {"verdict": "??? "}})
    assert stats["unmapped_verdicts"] == ["a-grounding"]
    assert {entry["id"]: entry for entry in entries}["a-grounding"]["human"] is None


def test_scored_agreement_after_injection():
    entries = parse_sheet(SYNTHETIC_SHEET)
    apply_verdicts(entries, {"a-grounding": {"verdict": "correct"}})
    result = build_result(entries)

    # judge：条目1 correct↔忠实（同意）、条目2 wrong↔pass（不同意）
    assert result["agreement"]["judge"]["numerator"] == 1
    assert result["agreement"]["judge"]["denominator"] == 2
    # judge2：条目1 correct↔不忠实（不同意）、条目2 wrong↔review（同意）
    assert result["agreement"]["judge2"]["numerator"] == 1
    assert result["agreement"]["judge2"]["denominator"] == 2
    assert result["filled"] == 2
    assert result["blank"] == 0
