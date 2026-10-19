"""按当前法源库重算历史咨询引用的条级核验（默认只报告，不写回）。

背景：`legal_consultations.references_json[].verification` 是**生成当时**的快照。
补了法源条文语料（`legal_articles`）之后，老记录仍显示「无核验字段 / 该法源未收录条文级数据」，
规则层数字看不出改善。本脚本用**与生成时同一条代码路径**（`enrich_references` + 句级条号）
重算，先给对照报告；确认后再 `--apply` 写回（写回时在 verification 里留 `recomputed_at`）。

    python -B scripts/recompute_reference_verification.py                     # 只报告
    python -B scripts/recompute_reference_verification.py --fill-source-id    # 额外报告「按标题能匹配到法源」的引用
    python -B scripts/recompute_reference_verification.py --apply --fill-source-id

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
from app.models.legal import LegalArticle, LegalConsultation, LegalSource  # noqa: E402
from app.services.legal.legal_reference_service import enrich_references  # noqa: E402
from app.services.legal.legal_service import (  # noqa: E402
    _article_text_by_source,
    _sentence_citation_numbers,
    match_reference_to_source,
)


def _classify(ref: dict) -> str:
    verification = ref.get("verification")
    if not isinstance(verification, dict) or not verification:
        return "unmarked"
    return "verified" if verification.get("verified") is True else "unverified"


def _basis(ref: dict) -> str:
    verification = ref.get("verification") or {}
    return str(
        verification.get("verification_basis") or verification.get("verification_note") or ""
    )


def _citations(refs: list[dict]) -> list[str]:
    seen: list[str] = []
    for ref in refs:
        citation = str(ref.get("citation") or "").strip()
        if citation and citation not in seen:
            seen.append(citation)
    return seen[:6]


def main() -> int:
    parser = argparse.ArgumentParser(description="重算历史咨询引用的条级核验")
    parser.add_argument("--apply", action="store_true", help="写回 references_json（默认只报告）")
    parser.add_argument(
        "--fill-source-id",
        action="store_true",
        help="重算前先用标题匹配给缺 source_id 的引用补上法源（写回时生效）",
    )
    parser.add_argument("--user-id", type=int, default=None, help="只看某个用户")
    parser.add_argument("--output", type=str, default=".tmp_recompute_report.json", help="报告 JSON 路径")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    db = SessionLocal()
    before: Counter[str] = Counter()
    after: Counter[str] = Counter()
    after_filled: Counter[str] = Counter()
    changes: list[dict] = []
    still_no_source: list[dict] = []
    filled_refs: list[dict] = []
    gap_by_source: dict[str, dict] = defaultdict(lambda: {"refs": 0, "consultations": set(), "citations": []})
    consultations = references = 0
    applied = 0

    try:
        query = db.query(LegalConsultation).filter(LegalConsultation.references_json.isnot(None))
        if args.user_id:
            query = query.filter(LegalConsultation.user_id == args.user_id)
        rows = query.order_by(LegalConsultation.id).all()
        sources_by_user: dict[int, list[LegalSource]] = {}
        for row in rows:
            try:
                refs = [item for item in (json.loads(row.references_json or "[]") or []) if isinstance(item, dict)]
            except ValueError:
                continue
            if not refs:
                continue
            consultations += 1
            references += len(refs)
            try:
                attribution = json.loads(row.attribution_json or "{}")
            except ValueError:
                attribution = {}
            sentence_numbers = _sentence_citation_numbers(refs, attribution if isinstance(attribution, dict) else {})

            recomputed = enrich_references(db, refs, sentence_numbers=sentence_numbers)

            if row.user_id not in sources_by_user:
                sources_by_user[row.user_id] = (
                    db.query(LegalSource).filter(LegalSource.user_id == row.user_id).all()
                )
            sources = sources_by_user[row.user_id]

            working = [dict(item) for item in refs]
            for item in working:
                if item.get("source_id"):
                    continue
                candidate = match_reference_to_source(item, sources)
                if candidate is not None:
                    item["source_id"] = candidate.id
            filled = enrich_references(db, working, sentence_numbers=sentence_numbers)
            article_text = _article_text_by_source(db, recomputed)

            for index, (old, new, new_filled) in enumerate(zip(refs, recomputed, filled)):
                before[_classify(old)] += 1
                after[_classify(new)] += 1
                after_filled[_classify(new_filled)] += 1
                if _classify(old) != _classify(new):
                    changes.append(
                        {
                            "consultation_id": row.id,
                            "index": index,
                            "title": new.get("title"),
                            "citation": new.get("citation"),
                            "before": _classify(old),
                            "after": _classify(new),
                            "basis": _basis(new),
                        }
                    )
                if not new.get("source_id"):
                    candidate = match_reference_to_source(new, sources)
                    still_no_source.append(
                        {
                            "consultation_id": row.id,
                            "index": index,
                            "title": new.get("title"),
                            "citation": new.get("citation"),
                            "matched_source_id": candidate.id if candidate else None,
                            "matched_has_articles": bool(
                                candidate and db.query(LegalArticle).filter(LegalArticle.source_id == candidate.id).count()
                            )
                            if candidate
                            else False,
                        }
                    )
                    if candidate is not None:
                        filled_refs.append(
                            {
                                "consultation_id": row.id,
                                "index": index,
                                "title": new.get("title"),
                                "source_id": candidate.id,
                                "source_title": candidate.title,
                                "after": _classify(new_filled),
                                "basis": _basis(new_filled),
                            }
                        )
                elif not article_text.get(new.get("source_id")):
                    key = str(new.get("title") or f"source#{new.get('source_id')}")
                    bucket = gap_by_source[key]
                    bucket["refs"] += 1
                    bucket["consultations"].add(row.id)
                    bucket["source_id"] = new.get("source_id")
                    for citation in _citations([new]):
                        if citation not in bucket["citations"]:
                            bucket["citations"].append(citation)

            if args.apply:
                payload = filled if args.fill_source_id else recomputed
                for item in payload:
                    verification = item.get("verification")
                    if isinstance(verification, dict):
                        verification["recomputed_at"] = now
                row.references_json = json.dumps(payload, ensure_ascii=False)
                applied += 1
        if args.apply and applied:
            db.commit()
    finally:
        db.close()

    report = {
        "generated_at": now,
        "mode": "apply" if args.apply else "dry-run",
        "fill_source_id": bool(args.fill_source_id),
        "totals": {
            "consultations": consultations,
            "references": references,
            "before": dict(before),
            "after": dict(after),
            "after_with_fill_source_id": dict(after_filled),
            "changed": len(changes),
            "refs_without_source_id": len(still_no_source),
            "refs_fillable_by_title": len(filled_refs),
            "applied_consultations": applied,
        },
        "changes": changes,
        "fillable_refs": filled_refs,
        "refs_without_source_id": still_no_source,
        "sources_missing_articles": [
            {
                "title": title,
                "source_id": bucket.get("source_id"),
                "refs": bucket["refs"],
                "consultations": len(bucket["consultations"]),
                "citations": bucket["citations"],
            }
            for title, bucket in sorted(gap_by_source.items(), key=lambda item: -item[1]["refs"])
        ],
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"模式：{report['mode']}｜咨询 {consultations} 条、引用 {references} 条")
    print(f"核验分布（重算前）：{dict(before)}")
    print(f"核验分布（重算后）：{dict(after)}")
    if args.fill_source_id:
        print(f"核验分布（重算 + 补 source_id）：{dict(after_filled)}")
    print(f"结论变化 {len(changes)} 条｜缺 source_id {len(still_no_source)} 条（其中按标题可匹配 {len(filled_refs)} 条）")
    print(f"仍查不到条文正文的法源 {len(report['sources_missing_articles'])} 个（语料采购清单）")
    for item in report["sources_missing_articles"][:8]:
        print(f"  - {item['title']}（source {item['source_id']}）：{item['refs']} 条引用、{item['consultations']} 条咨询")
    print(f"报告：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
