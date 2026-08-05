"""M-3 免费版转化 A/B 判定（docs/m3-conversion-ab-draft.md §3 执行口径）。

从真实库出数，按 user_id % 2 分 A/B 组（A=偶/B=奇，试点冻结），对比
升级转化率与 7 天留存，输出推广/保持/样本不足结论。

口径（与 pilot_weekly_report.py 一致）：
  - 排除供给账号（pilot% 组织成员）
  - 注册 = users.role != 'admin' 且非供给
  - 转化 = operation_logs 中 module=subscription & action=upgrade_intent（#81）
  - 7 天留存 = 注册后 [7,14) 天窗口内 ≥1 次咨询/审查/文书
  - 显著检验 = scipy.stats.chi2_contingency（p<0.05）

用法：
    python -B scripts/evaluate_ab_conversion.py [--min-sample 30] [--output docs/ab-result.md]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent

SUPPLY_ORG_SQL = "SELECT user_id FROM organization_members m JOIN organizations o ON o.id = m.organization_id WHERE o.code LIKE 'pilot%'"

# 7 天留存涉及的"活跃"任务表（与 dashboard /api/admin/retention 口径一致）
ACTIVITY_TABLES = ("legal_consultations", "legal_contract_reviews", "legal_drafts")


def _rows(engine, sql: str):
    def _convert(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        return v

    with engine.connect() as conn:
        return [dict((k, _convert(v)) for k, v in r._mapping.items()) for r in conn.execute(text(sql))]


def _task_rows_since(engine, since_iso: str) -> list[tuple[int, str]]:
    """返回 (user_id, created_at) 活动记录（三表合并，去重取最值）。"""
    rows = []
    for table in ACTIVITY_TABLES:
        rows.extend(_rows(engine, f"SELECT DISTINCT user_id, created_at FROM {table} WHERE created_at >= '{since_iso}'"))
    return [(int(r["user_id"]), r["created_at"]) for r in rows]


def _d7_active(created_at_iso: str, task_dates: list[datetime]) -> bool:
    created = datetime.fromisoformat(created_at_iso)
    lo = created + timedelta(days=7)
    hi = created + timedelta(days=14)
    return any(lo <= d < hi for d in task_dates)


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def collect(engine) -> dict:
    supply = [r["user_id"] for r in _rows(engine, SUPPLY_ORG_SQL)]
    supply_sql = "0" if not supply else ",".join(str(u) for u in supply)
    excl = f"AND u.id NOT IN ({supply_sql})"

    # 注册用户（含创建时间，用于分窗留存判定）
    registered = _rows(engine, f"""
        SELECT id, created_at FROM users u WHERE role != 'admin' {excl}
    """)

    # 升级意图（oplog #81）：module=subscription & action=upgrade_intent
    intents = _rows(engine, f"""
        SELECT o.user_id FROM operation_logs o
        WHERE o.module = 'subscription' AND o.action = 'upgrade_intent'
          AND o.user_id IS NOT NULL
    """)
    intent_user_ids = {int(r["user_id"]) for r in intents}

    # 活动记录（三表，用于 D7 留存）
    task_dates: dict[int, list[datetime]] = {}
    since = (datetime.utcnow() - timedelta(days=120)).isoformat()
    for uid, ts in _task_rows_since(engine, since):
        parsed = _parse_iso(ts)
        if parsed:
            task_dates.setdefault(uid, []).append(parsed)

    groups = {"A": {"registered": 0, "intent": 0, "d7_active": 0, "d7_observed": 0}, "B": {"registered": 0, "intent": 0, "d7_active": 0, "d7_observed": 0}}
    for row in registered:
        uid = int(row["id"])
        group = "A" if uid % 2 == 0 else "B"
        groups[group]["registered"] += 1
        if uid in intent_user_ids:
            groups[group]["intent"] += 1
        created = _parse_iso(row["created_at"])
        # 仅对已完整经历 D7 窗口的用户统计留存（now >= 注册+14 天）
        if created and datetime.utcnow() >= created + timedelta(days=14):
            groups[group]["d7_observed"] += 1
            if _d7_active(created.isoformat(), task_dates.get(uid, [])):
                groups[group]["d7_active"] += 1

    for g in groups.values():
        g["conversion_rate"] = round(g["intent"] / g["registered"], 4) if g["registered"] else 0.0
        g["d7_rate"] = round(g["d7_active"] / g["d7_observed"], 4) if g["d7_observed"] else None

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "excluded_supply_accounts": len(supply),
        "groups": groups,
        "total_registered": sum(g["registered"] for g in groups.values()),
    }


def decide(data: dict, min_sample: int = 30) -> dict:
    a, b = data["groups"]["A"], data["groups"]["B"]
    total = a["registered"] + b["registered"]
    if total < min_sample:
        return {
            "conclusion": "sample_insufficient",
            "reason": f"样本 {total} 人 < 阈值 {min_sample}，待试点真实用户到位后重跑",
        }
    from scipy.stats import chi2_contingency

    # 转化：[[A 转化, A 未转化], [B 转化, B 未转化]]
    table = [
        [a["intent"], a["registered"] - a["intent"]],
        [b["intent"], b["registered"] - b["intent"]],
    ]
    if sum(sum(row) for row in table) == 0:
        return {"conclusion": "no_data", "reason": "无注册用户"}
    _, p, _, _ = chi2_contingency(table)

    b_rate, a_rate = b["conversion_rate"], a["conversion_rate"]
    uplift = (b_rate - a_rate) / a_rate if a_rate > 0 else None
    significant = bool(p < 0.05)
    # 7 天留存不降（B >= A，均以非 None 计）
    d7_not_worse = bool((a["d7_rate"] is None) or (b["d7_rate"] is not None and b["d7_rate"] >= a["d7_rate"]))

    conclusion = "keep_status_quo"  # 无显著差异 → 保持现状，成本最低
    reason = "无显著差异"
    if significant and uplift is not None and uplift >= 0.30 and d7_not_worse:
        conclusion = "promote_b"
        reason = f"转化提升 {uplift*100:.1f}% 且显著（p={p:.3f}）且 7 天留存不降"
    elif significant and uplift is not None and uplift >= 0.30:
        conclusion = "mixed"
        reason = f"转化提升 {uplift*100:.1f}% 且显著（p={p:.3f}）但 7 天留存下降，需复验"

    return {
        "conclusion": conclusion,
        "reason": reason,
        "p_value": round(p, 4),
        "b_uplift_vs_a": round(uplift, 4) if uplift is not None else None,
        "significant": significant,
        "d7_not_worse": d7_not_worse,
    }


def render_markdown(data: dict, decision: dict) -> str:
    a, b = data["groups"]["A"], data["groups"]["B"]
    a_d7 = f"{a['d7_rate']:.2%}" if a["d7_rate"] is not None else "—"
    b_d7 = f"{b['d7_rate']:.2%}" if b["d7_rate"] is not None else "—"
    uplift = f"{decision['b_uplift_vs_a']*100:.1f}%" if decision.get("b_uplift_vs_a") is not None else "—"
    lines = [
        "# M-3 免费版转化 A/B 判定（docs/m3-conversion-ab-draft.md §3）",
        "",
        f"> 生成时间：{data['generated_at']}；已排除供给账号 {data['excluded_supply_accounts']} 个。",
        "",
        "## 1. 分组口径",
        "",
        "- 分群：`user_id % 2`（A=偶 / B=奇），试点期间冻结不换组",
        "- 转化 = oplog `upgrade_intent`；7 天留存 = 注册后 [7,14) 天 ≥1 次咨询/审查/文书",
        "",
        "## 2. 结果",
        "",
        "| 组 | 注册 | 升级意图 | 转化率 | D7 观察 | D7 留存 |",
        "|---|---|---|---|---|---|",
        f"| A（控制，5/2/2） | {a['registered']} | {a['intent']} | {a['conversion_rate']:.2%} | {a['d7_observed']} | {a_d7} |",
        f"| B（实验，8/3/3） | {b['registered']} | {b['intent']} | {b['conversion_rate']:.2%} | {b['d7_observed']} | {b_d7} |",
        "",
        "## 3. 判定",
        "",
        f"- **结论：{decision['conclusion']}**",
        f"- 原因：{decision['reason']}",
        f"- p 值：{decision.get('p_value', '—')}；B 相对 A 提升：{uplift}；7 天留存不降：{decision.get('d7_not_worse', '—')}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="M-3 free-tier conversion A/B judgment")
    parser.add_argument("--min-sample", type=int, default=30, help="Minimum registered users before judging")
    parser.add_argument("--output", default=None, help="Write markdown judgment to this path")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print(json.dumps({"status": "error", "message": "DATABASE_URL is not set"}))
        return 2
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        data = collect(engine)
    finally:
        engine.dispose()
    decision = decide(data, min_sample=args.min_sample)

    if args.output:
        out = ROOT / args.output
        out.write_text(render_markdown(data, decision), encoding="utf-8")
        print(f"markdown -> {out}")
    print(json.dumps({"status": "ok", "data": data, "decision": decision}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
