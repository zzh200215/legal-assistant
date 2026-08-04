"""#44 试点启动基线快照：试点启动前采集一次全口径基线，供周报对比。

从生产库直接取数（不依赖服务进程），输出 data/pilot-baseline-YYYYMMDD.json。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def _redact(value: str) -> str:
    url = make_url(value)
    password = url.password or ""
    redacted = url.render_as_string(hide_password=True)
    return redacted.replace(password, "****") if password else redacted


def collect(engine) -> dict:
    def scalar(sql: str) -> int:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar() or 0

    def rows(sql: str):
        with engine.connect() as conn:
            return [dict((k, float(v) if isinstance(v, Decimal) else v) for k, v in r._mapping.items()) for r in conn.execute(text(sql))]

    counts = {
        table: scalar(f"SELECT COUNT(*) FROM {table}")
        for table in [
            "users", "organizations", "legal_cases", "legal_consultations",
            "legal_contract_reviews", "legal_drafts", "legal_review_actions",
            "llm_call_logs", "token_usage", "quota_usages", "user_subscriptions",
        ]
    }
    counts["non_admin_users"] = scalar("SELECT COUNT(*) FROM users WHERE role != 'admin'")

    funnel = rows("""
        SELECT
          (SELECT COUNT(*) FROM users WHERE role != 'admin') AS registered,
          (SELECT COUNT(*) FROM legal_consultations) AS first_consultation,
          (SELECT COUNT(*) FROM legal_contract_reviews) AS first_contract_review,
          (SELECT COUNT(*) FROM legal_drafts) AS first_draft,
          (SELECT COUNT(*) FROM legal_review_actions WHERE action IN ('approve', 'return', 'offline')) AS reviewed_decisions
    """)[0]

    pilot_orgs = rows("""
        SELECT o.code, COUNT(m.id) AS members,
               SUM(CASE WHEN u.role = 'dept_admin' THEN 1 ELSE 0 END) AS lawyers
        FROM organizations o
        LEFT JOIN organization_members m ON m.organization_id = o.id
        LEFT JOIN users u ON u.id = m.user_id
        WHERE o.code LIKE 'pilot%'
        GROUP BY o.code, o.id ORDER BY o.code
    """)

    cost_rows = rows("""
        SELECT model, action, COUNT(*) AS calls,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens
        FROM token_usage
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        GROUP BY model, action
    """)
    pricing = {"qwen-plus": (0.004, 0.012), "qwen-turbo": (0.001, 0.003), "text-embedding-v3": (0.0005, 0.0)}
    for item in cost_rows:
        rate = pricing.get(item["model"], (0.004, 0.012))
        prompt = float(item["prompt_tokens"] or 0)
        completion = float(item["completion_tokens"] or 0)
        item["estimated_cost_cny"] = round(prompt / 1000 * rate[0] + completion / 1000 * rate[1], 4)
    cost = round(sum(item["estimated_cost_cny"] for item in cost_rows), 4)

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "funnel": funnel,
        "pilot_orgs": pilot_orgs,
        "cost_30d_cny": cost,
        "cost_by_model_action": cost_rows,
    }


def main() -> int:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print(json.dumps({"status": "error", "message": "DATABASE_URL is not set"}))
        return 2
    output_dir = Path("data")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        baseline = collect(engine)
    finally:
        engine.dispose()
    output = output_dir / f"pilot-baseline-{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    output.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "ok", "file": str(output), "database": _redact(database_url),
                      "summary": {k: v for k, v in baseline.items() if k not in ("pilot_orgs", "funnel")}},
                     ensure_ascii=False, indent=2))
    print("pilot_orgs:", json.dumps(baseline["pilot_orgs"], ensure_ascii=False))
    print("funnel:", json.dumps(baseline["funnel"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
