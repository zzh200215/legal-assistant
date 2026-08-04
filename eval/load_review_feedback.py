"""AI-2: 审核反馈回流用例 → 评测数据集转换。

`scripts/export_review_feedback.py` 把律师审核决策（通过/退回/转线下）+ 批注
导出为 eval/review_feedback_eval.jsonl（tier=regression）。本模块将其转换为
`run_generation_eval.py` 可消费的 dataset 片段：

- 回流用例没有人工黄金答案，因此按律师决策推断结构性回归断言（可确定性复现）：
  - approve（通过）   ：输出非空 + 免责声明 + 不预测胜诉（咨询），防退化；
  - return（退回补充）：额外要求 AI 显式标注缺失事实（咨询）——已知缺陷样本，
                        若新输出仍不标注缺口则判 fail；
  - offline（转线下） ：风险提示至少达到 medium（咨询）；
- 分类/条款召回等依赖黄金标注的指标在回归用例上跳过（skip_category_check /
  structural_only）。
"""

from __future__ import annotations

import json
from pathlib import Path


def _consultation_gold(case: dict) -> dict:
    action = case.get("review_action")
    return {
        "skip_category_check": True,
        "must_not_fabricate_winrate": True,
        "refusal_expected": False,
        "inactive_sources_only": False,
        "citation_must_match_patterns": [],
        # return：律师判定 AI 漏了关键事实，回归时要求输出显式列出缺失事实
        "must_have_missing_facts": action == "return",
        # offline：律师判定需转线下，风险提示至少到 medium
        "risk_level_min": "medium" if action == "offline" else "low",
    }


def _contract_gold(case: dict) -> dict:
    return {
        "structural_only": True,
        "min_high_risk_count": 0,
        "must_not_fabricate_entities": [],
    }


def _draft_gold(case: dict) -> dict:
    return {
        "required_fields_must_appear": [],
        "placeholder_fields": [],
        "must_not_fabricate": [],
        "must_contain_disclaimer": True,
    }


def to_dataset_cases(cases: list[dict]) -> dict:
    """将回流用例列表转换为 run_generation_eval 的 dataset 片段（按 target_type 分组）。"""
    consultation_cases: list[dict] = []
    contract_review_cases: list[dict] = []
    draft_generation_cases: list[dict] = []

    for case in cases:
        target_type = case.get("target_type")
        if target_type == "consultation":
            consultation_cases.append(
                {
                    "id": case["id"],
                    "question": case.get("source") or "",
                    "regression": True,
                    "gold": _consultation_gold(case),
                }
            )
        elif target_type == "contract_review":
            contract_review_cases.append(
                {
                    "id": case["id"],
                    "contract_text": case.get("source") or "",
                    "regression": True,
                    "gold": _contract_gold(case),
                }
            )
        elif target_type == "draft":
            draft_generation_cases.append(
                {
                    "id": case["id"],
                    "document_type": case.get("document_type", "supplementary_agreement"),
                    "fields": {},
                    "missing_fields": [],
                    "regression": True,
                    "gold": _draft_gold(case),
                }
            )

    return {
        "contract_review_cases": contract_review_cases,
        "draft_generation_cases": draft_generation_cases,
        "consultation_cases": consultation_cases,
    }


def load_review_feedback(path: Path | str) -> dict:
    """读取回流 JSONL 并转换为评测 dataset 片段；文件不存在或为空返回空片段。"""
    path = Path(path)
    cases: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return to_dataset_cases(cases)
