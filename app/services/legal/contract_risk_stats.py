"""跨审查风险统计：把多条合同审查的 `risks_json` 汇总成一份只读视图。

风险只存一份（`legal_contract_reviews.risks_json`，见 step26），所以统计不需要新表，
直接在读取时聚合。作用域由调用方决定：
- 不传 case_id：本人工作台（list_rows 默认按提交人隔离）；
- 传 case_id：该案件下可见记录（方案 B，案件成员可见）。

口径与证据链一致：`anchored` 取风险上服务端下发的 `evidence.anchored`；状态经
`risk_status()` 归一（高风险不会停在 open）。数字都是**派生的**，不落库、不可写。
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.core.time import utc_now
from app.services.legal.legal_domain_service import SEVERITIES, risk_status

SEVERITY_ORDER = ("critical", "high", "medium", "low")
STATUS_ORDER = ("needs_review", "open", "accepted", "mitigated", "dismissed")
PENDING_STATUSES = ("open", "needs_review")
HIGH_SEVERITIES = ("high", "critical")
TOP_CLAUSES = 5
TREND_MAX_BUCKETS = 12
TREND_DEFAULT_BUCKETS = 8  # days=None 时按近 8 周看趋势


def _clause_label(risk: dict) -> str:
    return str(risk.get("clause_type") or "other")


def _as_naive_utc(value) -> datetime | None:
    """created_at 可能是 datetime 或 ISO 字符串（JSON 序列化后），统一成 naive UTC。"""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _within_window(rows: list[dict], days: int | None, now: datetime) -> list[dict]:
    if not days:
        return rows
    cutoff = now - timedelta(days=days)
    kept = []
    for row in rows:
        created = _as_naive_utc(row.get("created_at"))
        if created is not None and created >= cutoff:
            kept.append(row)
    return kept


def _weekly_trend(rows: list[dict], days: int | None, now: datetime) -> list[dict]:
    """按周分桶（最新一周在最后），桶数 = ceil(days/7)，封顶 12。"""
    span = days or TREND_DEFAULT_BUCKETS * 7
    weeks = max(1, min(TREND_MAX_BUCKETS, math.ceil(span / 7)))
    buckets = []
    for offset in range(weeks - 1, -1, -1):
        start = now - timedelta(days=(offset + 1) * 7)
        buckets.append({
            "week_start": start.date().isoformat(),
            "reviews": 0,
            "risks": 0,
            "high": 0,
        })
    for row in rows:
        created = _as_naive_utc(row.get("created_at"))
        if created is None:
            continue
        age_days = max(0, (now - created).days)
        bucket_index = weeks - 1 - min(weeks - 1, age_days // 7)
        bucket = buckets[bucket_index]
        bucket["reviews"] += 1
        risks = [risk for risk in (row.get("risks") or []) if isinstance(risk, dict)]
        bucket["risks"] += len(risks)
        bucket["high"] += sum(1 for risk in risks if risk.get("risk_level") in HIGH_SEVERITIES)
    return buckets


def summarize_contract_risk_stats(
    rows: list[dict], *, days: int | None = None, now: datetime | None = None,
) -> dict:
    """rows 为 `serialize_workspace_row` 的返回值列表（含 risks / evidence_summary）。

    `days` 给定时只统计该时间窗内的审查（按 created_at），并附带按周的 `trend`。
    """
    now = now or utc_now()
    rows = _within_window([row for row in rows or [] if isinstance(row, dict)], days, now)
    reviews = rows
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
        "window": {"days": days},
        "trend": _weekly_trend(reviews, days, now),
        "generated_at": now.isoformat(),
    }
