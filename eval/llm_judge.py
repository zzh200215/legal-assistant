"""Optional LLM-as-Judge scoring for offline RAG evaluation.

The deterministic retrieval and citation metrics remain the primary scores.
Judge scores are explicitly marked as model-based auxiliary signals.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.llm.llm_service import llm_service


_SCORE_FIELDS = ("groundedness", "answer_relevance", "completeness")
_VERDICTS = {"pass", "review", "fail"}


def _clip(value: Any, limit: int = 6000) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _build_prompt(case: dict[str, Any]) -> str:
    citations = case.get("citations") if isinstance(case.get("citations"), list) else []
    citation_text = "\n".join(_clip(item.get("source_text"), 1200) for item in citations if isinstance(item, dict))
    return (
        "你是企业 RAG 离线评测裁判。只能依据给定的参考答案和引用片段评分，"
        "不能补充外部知识。对拒答样本，重点判断是否恰当拒答。\n"
        "只输出 JSON："
        '{"groundedness":0.0,"answer_relevance":0.0,"completeness":0.0,'
        '"verdict":"pass|review|fail","reason":"不超过80字"}\n\n'
        f"问题：{_clip(case.get('question'), 1200)}\n"
        f"应拒答：{bool(case.get('should_refuse'))}\n"
        f"参考答案：{_clip(case.get('reference_answer'), 2000)}\n"
        f"系统回答：{_clip(case.get('answer'), 3000)}\n"
        f"引用片段：{citation_text or '无'}"
    )


def _normalize_judgement(payload: dict[str, Any]) -> dict[str, Any]:
    scores = {}
    for field in _SCORE_FIELDS:
        try:
            scores[field] = round(max(0.0, min(float(payload.get(field)), 1.0)), 4)
        except (TypeError, ValueError):
            scores[field] = None
    verdict = str(payload.get("verdict") or "review").strip().lower()
    return {
        **scores,
        "verdict": verdict if verdict in _VERDICTS else "review",
        "reason": _clip(payload.get("reason"), 200),
    }


async def judge_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score cases sequentially to control cost and avoid evaluator rate bursts."""
    results = []
    for index, case in enumerate(cases):
        try:
            raw = await llm_service.generate(
                _build_prompt(case),
                temperature=0.0,
                action="eval_llm_judge",
            )
            payload = llm_service.parse_json_object(raw)
            if not payload:
                raise ValueError("invalid_judge_json")
            results.append({"case_index": index, "available": True, **_normalize_judgement(payload)})
        except Exception:
            results.append(
                {
                    "case_index": index,
                    "available": False,
                    "groundedness": None,
                    "answer_relevance": None,
                    "completeness": None,
                    "verdict": "review",
                    "reason": "judge_unavailable",
                }
            )
    return results


def summarize_judgements(judgements: list[dict[str, Any]]) -> dict[str, Any]:
    available = [item for item in judgements if item.get("available")]
    summary: dict[str, Any] = {
        "enabled": True,
        "available": bool(available),
        "judged_cases": len(available),
        "unavailable_cases": len(judgements) - len(available),
    }
    for field in _SCORE_FIELDS:
        scores = [float(item[field]) for item in available if item.get(field) is not None]
        summary[field] = round(sum(scores) / len(scores), 4) if scores else None
    summary["pass_rate"] = (
        round(sum(item.get("verdict") == "pass" for item in available) / len(available), 4)
        if available
        else None
    )
    return summary
