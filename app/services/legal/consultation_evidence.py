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
