"""咨询侧证据链摘要：把引用 / 句级溯源 / 忠实度汇成一个可展示的等级。

口径（与前端「依据」标签、判官结论一致）：
- 引用 = `references_json` 条数；「有效引用」= status 为 active 的条数；
- 句级覆盖 = `attribution_json.matched_sentences / total_sentences`（服务端生成时算好的，不重算）；
- 忠实度 = `faithfulness_json.available === true` 且有布尔 `faithful` 才给 pass/fail，否则 unchecked；
- 等级：no refs → none；忠实度 fail → weak；覆盖率 ≥80% 且引用有效 → strong；
  ≥50% 或只有整篇粒度 → partial；其余 weak。

只读派生，不写库、不迁移（与合同侧 `annotate_contract_evidence` 同思路）。
"""

from __future__ import annotations

import json

STRONG_RATE = 0.8
PARTIAL_RATE = 0.5


def _json(raw, fallback):
    if not raw:
        return fallback
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def summarize_consultation_evidence(row) -> dict:
    references = _json(getattr(row, "references_json", None), [])
    attribution = _json(getattr(row, "attribution_json", None), {})
    faithfulness = _json(getattr(row, "faithfulness_json", None), {})

    total_sentences = attribution.get("total_sentences")
    matched = attribution.get("matched_sentences")
    if not isinstance(total_sentences, int) or total_sentences <= 0:
        total_sentences = 0
        matched = 0
    elif not isinstance(matched, int) or matched < 0:
        matched = 0
    matched = min(matched, total_sentences)
    rate = round(matched / total_sentences, 4) if total_sentences else None

    active = sum(1 for ref in references if str(ref.get("status") or "").lower() == "active")
    if faithfulness.get("available") is True and isinstance(faithfulness.get("faithful"), bool):
        faith = "pass" if faithfulness["faithful"] else "fail"
    else:
        faith = "unchecked"
    unsupported = faithfulness.get("unsupported")
    unsupported_count = len(unsupported) if isinstance(unsupported, list) else 0

    if not references:
        level = "none"
    elif faith == "fail":
        level = "weak"
    elif total_sentences and rate is not None and rate >= STRONG_RATE and active:
        level = "strong"
    elif total_sentences and rate is not None and rate >= PARTIAL_RATE:
        level = "partial"
    elif total_sentences:
        level = "weak"
    else:
        # 老记录没有句级映射：有有效引用算部分可核，否则弱
        level = "partial" if active else "weak"

    return {
        "references": len(references),
        "active_references": active,
        "sentences": total_sentences,
        "attributed": matched,
        "attributed_rate": rate,
        "faithfulness": faith,
        "unsupported": unsupported_count,
        "level": level,
    }


def _reference_key(ref: dict) -> str:
    return str(ref.get("source_id") or ref.get("title") or "")


def _reference_digest(ref: dict) -> dict:
    return {"title": ref.get("title"), "source_id": ref.get("source_id")}


def compare_consultation_evidence(previous, current) -> dict:
    """追问前后对比：引用增删 + 句级覆盖 + 等级/忠实度变化（读时派生）。"""
    before = summarize_consultation_evidence(previous)
    after = summarize_consultation_evidence(current)
    prev_refs = [ref for ref in _json(getattr(previous, "references_json", None), []) if isinstance(ref, dict)]
    cur_refs = [ref for ref in _json(getattr(current, "references_json", None), []) if isinstance(ref, dict)]
    prev_map = {_reference_key(ref): ref for ref in prev_refs}
    cur_map = {_reference_key(ref): ref for ref in cur_refs}
    added = [_reference_digest(cur_map[key]) for key in cur_map.keys() - prev_map.keys()]
    removed = [_reference_digest(prev_map[key]) for key in prev_map.keys() - cur_map.keys()]

    def percent(summary: dict) -> int | None:
        return None if summary["attributed_rate"] is None else round(summary["attributed_rate"] * 100)

    before_rate, after_rate = percent(before), percent(after)
    if before_rate is None and after_rate is None:
        coverage = "未生成句级溯源"
    else:
        coverage = f"句级覆盖 {before_rate if before_rate is not None else '—'}% → {after_rate if after_rate is not None else '—'}%"
    summary_text = (
        f"引用 {before['references']} → {after['references']}（+{len(added)}/-{len(removed)}）"
        f" · {coverage} · 等级 {before['level']} → {after['level']}"
    )
    return {
        "parent_id": getattr(previous, "id", None),
        "references": {
            "before": before["references"],
            "after": after["references"],
            "added": added,
            "removed": removed,
        },
        "attribution": {
            "before": {"matched": before["attributed"], "total": before["sentences"], "rate": before_rate},
            "after": {"matched": after["attributed"], "total": after["sentences"], "rate": after_rate},
        },
        "level": {"before": before["level"], "after": after["level"]},
        "faithfulness": {"before": before["faithfulness"], "after": after["faithfulness"]},
        "summary": summary_text,
    }


def attach_consultation_comparison(db, payload: dict, row) -> dict:
    """给咨询 payload 挂上「与追问来源的对比」；没有 parent 或父记录不存在时原样返回。"""
    parent_id = getattr(row, "parent_id", None)
    if not parent_id or not isinstance(payload, dict):
        return payload
    from app.models.legal import LegalConsultation

    parent = db.get(LegalConsultation, parent_id)
    if parent is not None:
        payload["comparison"] = compare_consultation_evidence(parent, row)
    return payload
