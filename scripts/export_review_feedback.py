"""AI-2: 律师审核反馈回流评测闭环。

将律师在审核队列中做出的决策（通过 / 退回补充事实 / 转线下）+ 审核意见，
与对应的 AI 输出（咨询建议 / 合同审查意见 / 文书草稿）配对，抽取为
可回归评测的用例（JSONL）。试点运行期间定期执行，让 AI 质量随使用量自我提升。

增量去重：
- state 文件记录 last_action_id，只导出其上新增的审核动作；
- 同一目标（target_type+target_id）在一批内仅保留最近一次动作。

用法:
    python -B scripts/export_review_feedback.py --dry-run
        # 只统计可导出数量，不写文件
    python -B scripts/export_review_feedback.py
        # 追加导出增量到 eval/review_feedback_eval.jsonl，并推进 state
    python -B scripts/export_review_feedback.py --since-id 100 --limit 200 --output out.jsonl
        # 指定起点、数量与输出路径

执行前建议先运行 scripts/create_pilot_backup.py 备份（本脚本只读审核数据与目标记录）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.legal import (  # noqa: E402
    ContractReview,
    LegalConsultation,
    LegalDraft,
    LegalReviewAction,
)
from app.services.legal_service import target_query  # noqa: E402

DEFAULT_ACTIONS = ("approve", "return", "offline")
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "eval" / "review_feedback_eval.jsonl"
DEFAULT_STATE = Path(__file__).resolve().parents[1] / "scripts" / "review_feedback_state.json"
AI_OUTPUT_CHARS = 800


def extract_source(row, target_type: str) -> str:
    """从目标记录提取用户输入（评测输入）。"""
    if isinstance(row, LegalConsultation):
        return row.question or ""
    if isinstance(row, ContractReview):
        title = row.title or ""
        content = row.content or ""
        return f"{title}\n{content}" if title else content
    if isinstance(row, LegalDraft):
        try:
            fields = json.loads(row.fields_json or "{}")
        except (TypeError, json.JSONDecodeError):
            fields = {}
        summary = "；".join(f"{k}: {v}" for k, v in fields.items() if v)
        return f"{row.title or ''}\n{summary}"
    return ""


def extract_ai_output(row, target_type: str) -> str:
    """从目标记录提取 AI 输出摘要（评测输出，截断防膨胀）。"""
    if isinstance(row, LegalConsultation):
        return (row.advice or "")[:AI_OUTPUT_CHARS]
    if isinstance(row, ContractReview):
        parts = [row.summary or ""]
        try:
            risks = json.loads(row.risks_json or "[]")
        except (TypeError, json.JSONDecodeError):
            risks = []
        for item in risks:
            parts.append(f"[{item.get('risk_level', '')}] {item.get('label', '')}：{(item.get('description') or '')[:120]}")
        return "\n".join(parts)[:AI_OUTPUT_CHARS]
    if isinstance(row, LegalDraft):
        return (row.content or "")[:AI_OUTPUT_CHARS]
    return ""


def build_case(target_type: str, row, action_record: LegalReviewAction) -> dict:
    """将一次律师审核决策 + 对应 AI 输出配对为一条评测用例。"""
    case = {
        "id": f"rf-{target_type}-{row.id}",
        "tier": "regression",
        "target_type": target_type,
        "target_id": row.id,
        "source": extract_source(row, target_type),
        "ai_output": extract_ai_output(row, target_type),
        "review_action": action_record.action,
        "review_note": action_record.note,
        "from_status": action_record.from_status,
        "to_status": action_record.to_status,
        "reviewed_at": action_record.created_at.isoformat() if action_record.created_at else None,
    }
    # 带上评测回归复现所需的字段（draft 需要 document_type，consultation 需要 category）
    if isinstance(row, LegalDraft):
        case["document_type"] = row.document_type
    elif isinstance(row, LegalConsultation):
        case["category"] = row.category
    return case


def load_actions(db, *, after_id: int = 0, limit: int = 500, actions=DEFAULT_ACTIONS):
    return (
        db.query(LegalReviewAction)
        .filter(
            LegalReviewAction.id > after_id,
            LegalReviewAction.action.in_(list(actions)),
        )
        .order_by(LegalReviewAction.id.desc())
        .limit(limit)
        .all()
    )


def run(db, *, after_id: int = 0, limit: int = 500, actions=DEFAULT_ACTIONS) -> dict:
    """抽取审核反馈为评测用例，返回用例列表 + 游标 + 统计。

    记录按 ID 降序处理，同一目标首次出现即为其最近一次决策，因此去重后
    保留的是最新审核结论（通过/退回/转线下）。
    """
    records = load_actions(db, after_id=after_id, limit=limit, actions=actions)
    cases: list[dict] = []
    seen: set[tuple[str, int]] = set()
    by_action: dict[str, int] = {}
    by_type: dict[str, int] = {}
    skipped = 0
    for action_record in records:
        key = (action_record.target_type, action_record.target_id)
        if key in seen:
            continue  # 同一目标只保留最近一次动作
        seen.add(key)
        row = target_query(db, action_record.target_type, action_record.target_id)
        if row is None:
            skipped += 1
            continue
        cases.append(build_case(action_record.target_type, row, action_record))
        by_action[action_record.action] = by_action.get(action_record.action, 0) + 1
        by_type[action_record.target_type] = by_type.get(action_record.target_type, 0) + 1
    last_id = max((r.id for r in records), default=after_id)
    return {
        "cases": cases,
        "last_action_id": last_id,
        "stats": {
            "exported": len(cases),
            "by_action": by_action,
            "by_type": by_type,
            "skipped_missing_targets": skipped,
        },
    }


def _read_state(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("last_action_id", 0))
    except (ValueError, json.JSONDecodeError):
        return 0


def _write_state(path: Path, last_action_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_action_id": last_action_id}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-2 审核反馈回流评测用例导出")
    parser.add_argument("--since-id", type=int, default=None, help="起始审核动作 ID（缺省读 state 文件）")
    parser.add_argument("--limit", type=int, default=500, help="单次最多处理审核动作数")
    parser.add_argument("--actions", nargs="+", default=list(DEFAULT_ACTIONS), help="要回流的动作类型")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="评测用例输出 JSONL")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE, help="增量游标文件")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = parser.parse_args()

    from app.core.database import SessionLocal

    after_id = args.since_id if args.since_id is not None else _read_state(args.state_file)
    with SessionLocal() as db:
        result = run(db, after_id=after_id, limit=args.limit, actions=tuple(args.actions))

    stats = result["stats"]
    print(json.dumps({"mode": "dry-run" if args.dry_run else "export", "since_action_id": after_id, **stats}, ensure_ascii=False, indent=2))

    if args.dry_run:
        return 0

    if not result["cases"]:
        print(json.dumps({"status": "no_new_cases"}, ensure_ascii=False))
        return 0

    new_cases = result["cases"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                existing.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                continue
    fresh = [case for case in new_cases if case["id"] not in existing]
    if not fresh:
        print(json.dumps({"status": "all_cases_already_exported", "duplicates": len(new_cases)}, ensure_ascii=False))
        _write_state(args.state_file, result["last_action_id"])
        return 0

    with args.output.open("a", encoding="utf-8") as handle:
        for case in fresh:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    _write_state(args.state_file, result["last_action_id"])

    print(
        json.dumps(
            {"status": "ok", "exported": len(fresh), "duplicates": len(new_cases) - len(fresh),
             "output": str(args.output), "last_action_id": result["last_action_id"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
