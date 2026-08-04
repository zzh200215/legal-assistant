"""#72/退出问卷与 NPS 导出脚本: 试点退出调查 CSV 导出（管理端用）

用法:
    python scripts/export_exit_surveys.py --output data/exit-surveys-2026-09-01.csv
"""
import argparse
import csv
import io
import os

from sqlalchemy import create_engine, text


def _read_env(key: str) -> str:
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        if os.path.exists(path):
            for line in io.open(path, encoding="utf-8"):
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/exit-surveys.csv")
    parser.add_argument("--since", default=None, help="YYYY-MM-DD, optional")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip() or _read_env("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set (env or .env)")
        return 2

    engine = create_engine(database_url)
    sql = """
        SELECT s.id, s.user_id, u.username, s.org_id, s.nps_score,
               s.trust_confidence, s.trust_citations, s.trust_next_steps,
               s.value_ranking, s.review_wish, s.pain_point, s.pay_intent,
               s.feature_requests, s.summary_feedback, s.created_at
        FROM exit_surveys s
        LEFT JOIN users u ON u.id = s.user_id
    """
    params = {}
    if args.since:
        sql += " WHERE s.created_at >= :since"
        params["since"] = args.since
    sql += " ORDER BY s.created_at DESC"
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
        cols = list(rows[0]._mapping.keys()) if rows else [
            "id", "user_id", "username", "org_id", "nps_score", "trust_confidence",
            "trust_citations", "trust_next_steps", "value_ranking", "review_wish",
            "pain_point", "pay_intent", "feature_requests", "summary_feedback", "created_at",
        ]

    out = args.output
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with io.open(out, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        for r in rows:
            writer.writerow([r._mapping[c] for c in cols])
    print(f"exported {len(rows)} surveys -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
