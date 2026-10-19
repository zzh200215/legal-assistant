"""给历史咨询回填「证据可核验性」摘要（默认只报告，`--apply` 才写回）。

背景：`evidence_json` 是 2026-10-19 才加的列，之前生成的答复都没有——系统页那张卡片
因此分母很小（覆盖面 4%）。本脚本按**当前法源库**给老记录补算同一口径的摘要。

口径与「生成时算的」**必须区分开**，所以写回的摘要带两个额外字段：

- `basis: "recomputed"`：这条摘要是事后按当前法源库重算的，不是生成当时的事实；
- `computed_at`：重算时间。

前端据此在说明里标「事后重算」，聚合里也单独计数（生成时 / 事后重算分开看）。
**不改写 `references_json[].verification`**——那是生成当时的快照，改它等于改历史
（用户 2026-10-19 已明确选择保留历史快照）。

    python -B scripts/backfill_consultation_evidence.py                      # 只报告
    python -B scripts/backfill_consultation_evidence.py --user-id 37
    python -B scripts/backfill_consultation_evidence.py --apply

本机 python 子进程写不了工作区子目录：报告默认落仓库根，再自行 Move-Item。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.models.legal import LegalConsultation, LegalSource  # noqa: E402
from app.services.legal.legal_reference_service import enrich_references, evidence_summary  # noqa: E402
from app.services.legal.legal_service import (  # noqa: E402
    _sentence_citation_numbers,
    match_reference_to_source,
)

BASIS_RECOMPUTED = "recomputed"


def _json_object(payload: str | None) -> dict:
    if not payload:
        return {}
    try:
        value = json.loads(payload)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(payload: str | None) -> list[dict]:
    if not payload:
        return []
    try:
        value = json.loads(payload)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def has_material(trace_json: str | None) -> bool:
    """老记录有没有「本次参考了材料」这件事，只能从 trace 的 contexts 看（生成时写入）。"""
    contexts = _json_object(trace_json).get("contexts")
    return isinstance(contexts, list) and bool(contexts)


def build_evidence(db, row, *, sources: list[LegalSource], now: str) -> dict | None:
    """按当前法源库给一条历史咨询算证据可核验性摘要（不写库，纯计算）。

    走**与生成时同一条代码路径**：句级条号合并 → 引用按标题补 source_id → `enrich_references`。
    没有引用的答复返回 None（老记录里 `references_json` 为 `[]` 的不少，不该硬塞一个「未引用法源」）。
    """
    refs = _json_list(row.references_json)
    if not refs:
        return None
    attribution = _json_object(row.attribution_json)
    sentence_numbers = _sentence_citation_numbers(refs, attribution)

    working = [dict(item) for item in refs]
    for item in working:
        if item.get("source_id"):
            continue
        candidate = match_reference_to_source(item, sources)
        if candidate is not None:
            item["source_id"] = candidate.id

    enriched = enrich_references(db, working, sentence_numbers=sentence_numbers)
    summary = evidence_summary(enriched, materials=1 if has_material(row.trace_json) else 0)
    summary["basis"] = BASIS_RECOMPUTED
    summary["computed_at"] = now
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="回填历史咨询的证据可核验性摘要")
    parser.add_argument("--apply", action="store_true", help="写回 evidence_json（默认只报告）")
    parser.add_argument("--user-id", type=int, default=None, help="只处理某个用户")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少条（0 = 不限）")
    parser.add_argument("--output", type=str, default=".tmp_evidence_backfill_report.json")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    db = SessionLocal()
    levels: Counter[str] = Counter()
    per_user: dict[int, Counter[str]] = defaultdict(Counter)
    scanned = with_refs = skipped = applied = materials = 0
    samples: list[dict] = []

    try:
        query = db.query(LegalConsultation).filter(LegalConsultation.evidence_json.is_(None))
        if args.user_id:
            query = query.filter(LegalConsultation.user_id == args.user_id)
        query = query.order_by(LegalConsultation.id)
        if args.limit:
            query = query.limit(args.limit)
        rows = query.all()

        sources_by_user: dict[int, list[LegalSource]] = {}
        for row in rows:
            scanned += 1
            if row.user_id not in sources_by_user:
                sources_by_user[row.user_id] = (
                    db.query(LegalSource).filter(LegalSource.user_id == row.user_id).all()
                )
            summary = build_evidence(db, row, sources=sources_by_user[row.user_id], now=now)
            if summary is None:
                skipped += 1
                continue
            with_refs += 1
            levels[summary["level"]] += 1
            per_user[row.user_id][summary["level"]] += 1
            if summary["materials"]:
                materials += 1
            if len(samples) < 10:
                samples.append(
                    {
                        "consultation_id": row.id,
                        "user_id": row.user_id,
                        "level": summary["level"],
                        "references": summary["references"],
                        "article_verified": summary["article_verified"],
                        "note": summary["note"],
                    }
                )
            if args.apply:
                row.evidence_json = json.dumps(summary, ensure_ascii=False)
                applied += 1
        if args.apply and applied:
            db.commit()
    finally:
        db.close()

    report = {
        "generated_at": now,
        "mode": "apply" if args.apply else "dry-run",
        "totals": {
            "scanned_without_evidence": scanned,
            "with_references": with_refs,
            "skipped_no_references": skipped,
            "with_material_context": materials,
            "applied": applied,
            "levels": dict(levels),
        },
        "per_user": {str(user_id): dict(counts) for user_id, counts in sorted(per_user.items())},
        "samples": samples,
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"模式：{report['mode']}｜待回填 {scanned} 条（有引用 {with_refs}、无引用跳过 {skipped}）")
    print(f"四档分布：{dict(levels)}｜带材料上下文 {materials} 条")
    print(f"按用户：{report['per_user']}")
    if args.apply:
        print(f"已写回 {applied} 条（basis={BASIS_RECOMPUTED}，不改 references_json）")
    print(f"报告：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
