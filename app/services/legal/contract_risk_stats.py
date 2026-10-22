"""跨审查风险统计：把多条合同审查的 `risks_json` 汇总成一份只读视图。

风险只存一份（`legal_contract_reviews.risks_json`，见 step26），所以统计不需要新表，
直接在读取时聚合。作用域由调用方决定：
- 不传 case_id：本人工作台（list_rows 默认按提交人隔离）；
- 传 case_id：该案件下可见记录（方案 B，案件成员可见）。

口径与证据链一致：`anchored` 取风险上服务端下发的 `evidence.anchored`；状态经
`risk_status()` 归一（高风险不会停在 open）。数字都是**派生的**，不落库、不可写。
"""

from __future__ import annotations

from collections import Counter

from app.core.time import utc_now
from app.services.legal.legal_domain_service import SEVERITIES, risk_status

SEVERITY_ORDER = ("critical", "high", "medium", "low")
STATUS_ORDER = ("needs_review", "open", "accepted", "mitigated", "dismissed")
PENDING_STATUSES = ("open", "needs_review")
HIGH_SEVERITIES = ("high", "critical")
TOP_CLAUSES = 5


def _clause_label(risk: dict) -> str:
    return str(risk.get("clause_type") or "other")


def summarize_contract_risk_stats(rows: list[dict]) -> dict:
    """rows 为 `serialize_workspace_row` 的返回值列表（含 risks / evidence_summary）。"""
    reviews = [row for row in rows or [] if isinstance(row, dict)]
    risks = [risk for row in reviews for risk in (row.get("risks") or []) if isinstance(risk, dict)]

    by_severity = Counter()
    by_status = Counter()
    by_clause = Counter()
    anchored = 0
    for risk in risks:
        severity = risk.get("risk_level")
        by_severity[severity if severity in SEVERITIES else "medium"] += 1
        by_status[risk_status(risk)] += 1
        by_clause[_clause_label(risk)] += 1
        evidence = risk.get("evidence") if isinstance(risk.get("evidence"), dict) else {}
        if evidence.get("anchored"):
            anchored += 1

    total = len(risks)
    pending_high = sum(
        1
        for risk in risks
        if risk.get("risk_level") in HIGH_SEVERITIES and risk_status(risk) in PENDING_STATUSES
    )

    by_level = Counter(
        str((row.get("evidence_summary") or {}).get("level") or "none") for row in reviews
    )
    return {
        "reviews": len(reviews),
        "risks": total,
        "by_severity": {severity: by_severity.get(severity, 0) for severity in SEVERITY_ORDER},
        "by_status": {status: by_status.get(status, 0) for status in STATUS_ORDER},
        "by_clause_type": [
            {"clause_type": clause, "count": count} for clause, count in by_clause.most_common(TOP_CLAUSES)
        ],
        "anchored": anchored,
        "unanchored": total - anchored,
        "located_rate": round(anchored / total, 4) if total else 0.0,
        "high_total": sum(by_severity.get(severity, 0) for severity in HIGH_SEVERITIES),
        "pending_high": pending_high,
        "reviews_with_gaps": sum(
            1 for row in reviews if int((row.get("evidence_summary") or {}).get("unanchored") or 0) > 0
        ),
        "by_evidence_level": {level: by_level.get(level, 0) for level in ("strong", "partial", "weak", "none")},
        "generated_at": utc_now().isoformat(),
    }
