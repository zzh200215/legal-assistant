"""模拟试点周报（不进库）：内置像样的模拟试点数据包，复用 #46 渲染产出周报。

场景：模拟 10 所律所试点第 2 周真实运行（10 所 × 2 = 20 供给账号被排除，
非供给注册律师 18 人，本周 3 人首次咨询），覆盖漏斗/成本/审核/AI-2 回流/
门户/NPS/退出问卷各口径。数据仅用于管线验证，不写数据库。

用法:
    python -B scripts/simulate_pilot_weekly_report.py
        # 默认 week-start=2026-08-10，输出 data/sim/ JSON + docs/ 下 -sim- 周报
    python -B scripts/simulate_pilot_weekly_report.py --week-start 2026-08-03 --output docs/x.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.pilot_weekly_report import render_markdown  # noqa: E402

PRICING = {"qwen-plus": (0.004, 0.012), "qwen-turbo": (0.001, 0.003), "text-embedding-v3": (0.0005, 0.0)}
# 周一~周日每天的调用量（calls, prompt_tokens, completion_tokens），qwen-plus 为主
DAILY_LOAD = [
    ("qwen-plus", 12, 8500, 2100),
    ("qwen-plus", 9, 6200, 1500),
    ("qwen-plus", 15, 9800, 2400),
    ("qwen-plus", 7, 4500, 1100),
    ("qwen-plus", 11, 7300, 1800),
    ("qwen-plus", 4, 2600, 700),
    ("qwen-plus", 6, 3800, 950),
]


def _cost_rows(week_start: str) -> list[dict]:
    """按周起点生成 7 天（周一~周日）LLM 调用流水，计价对齐 #46 口径。"""
    monday = date.fromisoformat(week_start)
    rows = []
    for offset, (model, calls, pt, ct) in enumerate(DAILY_LOAD):
        day = (monday + timedelta(days=offset)).isoformat()
        rate = PRICING.get(model, (0.004, 0.012))
        cost = pt / 1000 * rate[0] + ct / 1000 * rate[1]
        rows.append({
            "day": day, "model": model, "calls": calls,
            "prompt_tokens": pt, "completion_tokens": ct,
            "estimated_cost_cny": round(cost, 4),
        })
    return rows


def build_week_data(week_start: str) -> dict:
    """构造模拟试点第 2 周数据包（结构对齐 pilot_weekly_report.collect 返回值）。"""
    cost_by_day = _cost_rows(week_start)
    total_cost = round(sum(float(r["estimated_cost_cny"]) for r in cost_by_day), 4)
    return {
        "week_start": week_start,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "excluded_supply_accounts": 20,
        "funnel": {
            "registered_before": 15,
            "registered_total": 18,
            "first_consultation_week": 3,
            "week_start": week_start,
            "excluded_supply_accounts": 20,
        },
        "weekly_llm_cost_cny": total_cost,
        "weekly_llm_calls_by_day": cost_by_day,
        "review_actions_by_type": {"approve": 4, "return": 2, "offline": 1},
        "feedback": {"positive": 5, "negative": 1, "total": 6},
        "feedback_cursor_last_action_id": 47,
        "portal": {
            "access_count": 38,
            "active_links": 7,
            "denied_access": 2,
            "links_created": 3,
            "unique_visitors": 11,
            "repeat_visits": 4,
            "active_days": 6,
            "hourly_distribution": {8: 2, 9: 5, 10: 7, 11: 4, 14: 6, 15: 5, 16: 4, 17: 3, 20: 2},
        },
        "nps": {"week_responses": 4, "total_responses": 9, "nps": 50.0},
        "exit_surveys_week": {"cnt": 1, "renew": 1, "try_more": 0, "expensive": 0, "wont": 0},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate pilot weekly report (offline, no DB)")
    parser.add_argument("--week-start", default="2026-08-10", help="ISO date of simulated week start (default 2026-08-10)")
    parser.add_argument("--output", default=None, help="Markdown report path (default docs/pilot-weekly-report-sim-<week>.md)")
    args = parser.parse_args()

    report = build_week_data(args.week_start)
    json_path = ROOT / "data/sim" / f"pilot-weekly-report-sim-{args.week_start}.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    out = ROOT / (args.output or f"docs/pilot-weekly-report-sim-{args.week_start}.md")
    out.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps({
        "status": "ok", "mode": "simulated_offline", "week_start": args.week_start,
        "json": str(json_path), "markdown": str(out),
        "funnel": report["funnel"], "weekly_cost_cny": report["weekly_llm_cost_cny"],
        "review_actions": report["review_actions_by_type"],
        "portal_visitors": report["portal"]["unique_visitors"],
        "nps": report["nps"]["nps"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
