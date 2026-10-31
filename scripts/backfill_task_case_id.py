"""给历史任务回填 case_id（默认只报告，`--apply` 才写库）。

规则只补 `case_id IS NULL` 的任务，且只认可证明的来源：

1. `source_type=legal_consultation` → 咨询所属案件；
2. `source_type=document` → 文档 `metadata_json.case_id`；
3. 有 `parent_id` → 沿父任务链继承案件（支持多级拆解）。

meeting/chat 等没有可靠案件来源的任务保持为空；来源或案件已被删除的也不补。

    python -B scripts/backfill_task_case_id.py                       # dry-run
    python -B scripts/backfill_task_case_id.py --limit 500           # 只扫前 500 条
    python -B scripts/backfill_task_case_id.py --apply               # 写库
    python -B scripts/backfill_task_case_id.py --apply --output out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.services.jobs.task_case_backfill import backfill_task_case_ids  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="历史任务 case_id 回填（默认 dry-run）")
    parser.add_argument("--apply", action="store_true", help="实际写库（默认只出报告）")
    parser.add_argument("--limit", type=int, default=None, help="本次最多扫描多少条无 case_id 任务")
    parser.add_argument(
        "--output",
        default="task_case_backfill_report.json",
        help="报告输出路径（默认仓库根 task_case_backfill_report.json）",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须大于 0")

    db = SessionLocal()
    try:
        report = backfill_task_case_ids(db, apply=args.apply, limit=args.limit)
    finally:
        db.close()

    payload = report.as_dict()
    payload["mode"] = "apply" if args.apply else "dry-run"
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    mode = payload["mode"]
    print(
        f"模式：{mode}｜无 case_id 任务 {report.total_without_case} 条，"
        f"本次扫描 {report.scanned} 条，可回填 {report.planned} 条"
    )
    if report.by_rule:
        detail = " / ".join(f"{rule} {count}" for rule, count in sorted(report.by_rule.items()))
        print(f"按规则：{detail}")
    print(
        "跳过："
        f"无可靠来源 {report.skipped_no_source} / "
        f"来源无案件 {report.skipped_source_without_case} / "
        f"来源缺失 {report.skipped_missing_source} / "
        f"案件不存在 {report.skipped_missing_case}"
    )
    if args.apply:
        print(f"已写回 {report.applied} 条")
    print(f"报告：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
