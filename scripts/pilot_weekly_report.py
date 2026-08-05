"""#46 试点周报数据包：漏斗/留存/北极星/成本/AI-2 回流，五组口径从真实库出数。

关键：排除供给账号（pilot% 组织成员，内部测试/试点账号），防止污染漏斗与留存。
用法：
    python -B scripts/pilot_weekly_report.py [--week-start 2026-08-10] [--output docs/pilot-weekly-report.md]
输出：JSON 数据包 + 可选 Markdown 周报（对齐 docs/pilot-weekly-sample.md 格式）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent

SUPPLY_ORG_SQL = "SELECT user_id FROM organization_members m JOIN organizations o ON o.id = m.organization_id WHERE o.code LIKE 'pilot%'"


def _rows(engine, sql: str):
    def _convert(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        return v

    with engine.connect() as conn:
        return [dict((k, _convert(v)) for k, v in r._mapping.items()) for r in conn.execute(text(sql))]


def _monday(week_start: str | None) -> str:
    if week_start:
        return week_start
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


def collect(engine, week_start: str) -> dict:
    supply = [r["user_id"] for r in _rows(engine, SUPPLY_ORG_SQL)]
    supply_sql = "0" if not supply else ",".join(str(u) for u in supply)
    ex = f"AND u.id NOT IN ({supply_sql})" if supply else ""

    funnel = _rows(engine, f"""
        SELECT
          (SELECT COUNT(*) FROM users u WHERE role != 'admin' AND u.created_at < '{week_start}T00:00:00' {ex}) AS registered_before,
          (SELECT COUNT(*) FROM users u WHERE role != 'admin' {ex}) AS registered_total,
          (SELECT COUNT(DISTINCT user_id) FROM legal_consultations c JOIN users u ON u.id=c.user_id WHERE u.id NOT IN ({supply_sql}) AND c.created_at >= '{week_start}T00:00:00') AS first_consultation_week
    """)[0]
    funnel["week_start"] = week_start
    funnel["excluded_supply_accounts"] = len(supply)

    weekly_calls = _rows(engine, f"""
        SELECT DATE(created_at) AS day, model, COUNT(*) AS calls,
               COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens), 0) AS completion_tokens
        FROM token_usage
        WHERE created_at >= '{week_start}T00:00:00'
        GROUP BY day, model ORDER BY day
    """)
    pricing = {"qwen-plus": (0.004, 0.012), "qwen-turbo": (0.001, 0.003), "text-embedding-v3": (0.0005, 0.0)}
    total_cost = 0.0
    for item in weekly_calls:
        rate = pricing.get(item["model"], (0.004, 0.012))
        cost = float(item["prompt_tokens"]) / 1000 * rate[0] + float(item["completion_tokens"]) / 1000 * rate[1]
        item["estimated_cost_cny"] = round(cost, 4)
        total_cost += cost

    review_actions = _rows(engine, f"""
        SELECT action, COUNT(*) AS cnt
        FROM legal_review_actions
        WHERE created_at >= '{week_start}T00:00:00'
        GROUP BY action ORDER BY action
    """)
    reviews_by_action = {r["action"]: int(r["cnt"]) for r in review_actions}

    has_feedback_table = False
    with engine.connect() as conn:
        result = conn.execute(text("SHOW TABLES"))
        has_feedback_table = any("ai_output_feedback" in (row[0] or "") for row in result.fetchall())

    feedback = _rows(engine, f"""
        SELECT COALESCE(SUM(feedback_score >= 1), 0) AS positive,
               COALESCE(SUM(feedback_score < 1), 0) AS negative,
               COUNT(*) AS total
        FROM ai_output_feedback
    """) if has_feedback_table else None

    state_file = ROOT / "scripts/review_feedback_state.json"
    try:
        cursor = json.loads(state_file.read_text(encoding="utf-8")).get("last_action_id", 0)
    except Exception:  # noqa: BLE001
        cursor = 0

    # #85/portal 指标：周访问次数、独立令牌、新链接数（含被拒访问 result）
    portal = _rows(engine, f"""
        SELECT
          (SELECT COUNT(*) FROM legal_portal_access_logs WHERE accessed_at >= '{week_start}T00:00:00') AS access_count,
          (SELECT COUNT(DISTINCT portal_link_id) FROM legal_portal_access_logs WHERE accessed_at >= '{week_start}T00:00:00') AS active_links,
          (SELECT COUNT(*) FROM legal_portal_access_logs WHERE accessed_at >= '{week_start}T00:00:00' AND result IN ('denied','not_found','expired')) AS denied_access,
          (SELECT COUNT(*) FROM legal_portal_links WHERE created_at >= '{week_start}T00:00:00') AS links_created
    """)[0]

    # #95/P2-1 门户访问行为：去重访客（IP 哈希）、重复访客、时段分布
    portal_behavior = _rows(engine, f"""
        SELECT
          (SELECT COUNT(DISTINCT ip_hash) FROM legal_portal_access_logs WHERE accessed_at >= '{week_start}T00:00:00') AS unique_visitors,
          (SELECT COUNT(*) FROM (
             SELECT ip_hash, portal_link_id FROM legal_portal_access_logs
             WHERE accessed_at >= '{week_start}T00:00:00'
             GROUP BY ip_hash, portal_link_id HAVING COUNT(*) >= 2
           ) t) AS repeat_visits,
          (SELECT COUNT(DISTINCT DATE(accessed_at)) FROM legal_portal_access_logs
           WHERE accessed_at >= '{week_start}T00:00:00') AS active_days
    """)[0]
    hours = _rows(engine, f"""
        SELECT HOUR(accessed_at) AS h, COUNT(*) AS cnt
        FROM legal_portal_access_logs
        WHERE accessed_at >= '{week_start}T00:00:00'
        GROUP BY HOUR(accessed_at)
    """)
    portal["hourly_distribution"] = {int(h["h"]): int(h["cnt"]) for h in hours}
    portal["unique_visitors"] = int(portal_behavior["unique_visitors"])
    portal["repeat_visits"] = int(portal_behavior["repeat_visits"])
    portal["active_days"] = int(portal_behavior["active_days"])

    # #85/NPS 与退出问卷：周回收数 + 汇总分布
    nps_stats = _rows(engine, f"""
        SELECT
          (SELECT COUNT(*) FROM nps_responses WHERE created_at >= '{week_start}T00:00:00') AS week_responses,
          (SELECT COUNT(*) FROM nps_responses) AS total_responses,
          COALESCE((SELECT ROUND(100.0 * (SUM(score >= 9) - SUM(score <= 6)) / COUNT(*), 1) FROM nps_responses), 0) AS nps
    """)[0]
    survey_week = _rows(engine, f"""
        SELECT COUNT(*) AS cnt,
               COALESCE(SUM(pay_intent = 'renew'), 0) AS renew,
               COALESCE(SUM(pay_intent = 'try_more'), 0) AS try_more,
               COALESCE(SUM(pay_intent = 'expensive'), 0) AS expensive,
               COALESCE(SUM(pay_intent = 'wont'), 0) AS wont
        FROM exit_surveys
        WHERE created_at >= '{week_start}T00:00:00'
    """)[0]

    return {
        "week_start": week_start,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "excluded_supply_accounts": len(supply),
        "funnel": funnel,
        "weekly_llm_cost_cny": round(total_cost, 4),
        "weekly_llm_calls_by_day": weekly_calls,
        "review_actions_by_type": reviews_by_action,
        "feedback": feedback,
        "feedback_cursor_last_action_id": cursor,
        "portal": portal,
        "nps": nps_stats,
        "exit_surveys_week": survey_week,
    }


def render_markdown(report: dict) -> str:
    f = report["funnel"]
    lines = [
        f"# 试点周报（口径 #46，周起点 {report['week_start']}）",
        "",
        f"> 生成时间：{report['generated_at']}；已排除供给账号 {report['excluded_supply_accounts']} 个。",
        "",
        "## 1. 漏斗",
        "",
        f"- 注册（累计，非供给）：{int(f['registered_total'])}（周起点前 {int(f['registered_before'])}）",
        f"- 本周首次咨询用户：{int(f['first_consultation_week'])}",
        "",
        "## 2. 留存/北极星",
        "",
        "- 北极星口径（每周完成 ≥1 次 AI 辅助法律任务的有活跃案件律师数）：见 /api/admin/north-star",
        "",
        "## 3. AI-2 回流",
        "",
        f"- 本周审核动作：{json.dumps(report['review_actions_by_type'], ensure_ascii=False)}",
        f"- 回流游标 last_action_id：{int(report['feedback_cursor_last_action_id'])}",
        "",
        "## 4. 成本",
        "",
        f"- 本周 LLM 成本：¥{report['weekly_llm_cost_cny']}",
        f"- 按日/模型调用：{json.dumps(report['weekly_llm_calls_by_day'], ensure_ascii=False)}",
        "",
        "## 5. 质量反馈",
        "",
        f"- 端侧 👍/👎：{json.dumps(report['feedback'], ensure_ascii=False)}",
        "",
        "## 6. 客户门户（#85）",
        "",
        f"- 周访问：{int(report['portal']['access_count'])}（活跃链接 {int(report['portal']['active_links'])}，被拒 {int(report['portal']['denied_access'])})",
        f"- 本周新链接：{int(report['portal']['links_created'])}",
        f"- 访客/重复访问：去重访客 {int(report['portal']['unique_visitors'])}，重复访问 {int(report['portal']['repeat_visits'])}，活跃天数 {int(report['portal']['active_days'])}",
        f"- 时段分布（小时:次数）：{json.dumps(report['portal']['hourly_distribution'], ensure_ascii=False)}",
        "",
        "## 7. NPS 与退出问卷（#85）",
        "",
        f"- NPS 累计回收：{int(report['nps']['total_responses'])}（本周 {int(report['nps']['week_responses'])}），NPS={report['nps']['nps']}",
        f"- 本周退出问卷：{int(report['exit_surveys_week']['cnt'])} 份（续费 {int(report['exit_surveys_week']['renew'])} / 再试 {int(report['exit_surveys_week']['try_more'])} / 偏高 {int(report['exit_surveys_week']['expensive'])} / 不用 {int(report['exit_surveys_week']['wont'])}）",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pilot weekly report")
    parser.add_argument("--week-start", default=None, help="ISO date of week start (default: this Monday)")
    parser.add_argument("--output", default=None, help="Write markdown report to this path")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print(json.dumps({"status": "error", "message": "DATABASE_URL is not set"}))
        return 2
    week_start = _monday(args.week_start)
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        report = collect(engine, week_start)
    finally:
        engine.dispose()

    json_path = ROOT / f"data/pilot-weekly-report-{week_start}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.output:
        out = ROOT / args.output
        out.write_text(render_markdown(report), encoding="utf-8")
        print(f"markdown -> {out}")
    print(json.dumps({"status": "ok", "json": str(json_path), "week_start": week_start,
                      "excluded_supply_accounts": report["excluded_supply_accounts"],
                      "registered_total": report["funnel"]["registered_total"],
                      "first_consultation_week": report["funnel"]["first_consultation_week"],
                      "weekly_cost_cny": report["weekly_llm_cost_cny"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
