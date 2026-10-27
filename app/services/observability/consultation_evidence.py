"""证据可核验性在线指标的聚合（管理员观测用）。

数据源是 `legal_consultations.evidence_json`——生成时算好的确定性统计（见迁移
`20261019_0100` 与 `docs/review/step17-evidence-verifiability.md`）。这里只做聚合与
比率计算，不改写任何记录。

口径与前端一致：**没有 `evidence_json` 的老记录单独计数，不摊进比率分母**——
「没统计过」不等于「核验失败」。

除分布外还回答两个运营问题：
1. **趋势**：按天看四档分布与通过率，判断语料补齐/口径修正有没有在时间里体现；
2. **语料缺口**：哪些法源被引用最多却**一条条文都没有**（直接当采购清单用），
   以及有多少引用压根没挂到法源（`unmatched`）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.legal import LegalArticle, LegalConsultation, LegalSource
from app.models.user import User

# 与 `app/services/legal/legal_reference_service.py::_EVIDENCE_LEVELS` 同源；
# tests/test_consultation_evidence_stats.py 里有一条断言守着两处不漂移。
LEVEL_LABELS = {
    "strong": "引用已逐条核对",
    "partial": "部分引用已核对",
    "weak": "引用待核",
    "none": "未引用法源",
}

_REFERENCE_KEYS = (
    "references",
    "article_checked",
    "article_verified",
    "article_missing",
    "no_corpus",
    "no_numbers",
    "unmatched",
    "materials",
)

# 法源引用榜只展示前 N 个（前端是表格，不翻页）
SOURCE_LIMIT = 10

# 事后回填的摘要带这个 basis 标记（`scripts/backfill_consultation_evidence.py`）：
# 「按当前法源库重算」与「生成当时算的」是两件事，聚合里分开计数、界面上分开说。
RECOMPUTED_BASIS = "recomputed"

# 引用问题清单的枚举与展示文案（API 过滤器与前端标签共用一套口径）
ISSUE_LABELS = {
    "missing": "条号未找到",
    "no_corpus": "法源缺条文",
    "unmatched": "未挂法源",
    "weak": "答复待核",
}
ISSUE_ORDER = ("missing", "no_corpus", "unmatched", "weak")
SCOPE_LABELS = {
    "verified": "已核对",
    "missing": "条号未找到",
    "no_corpus": "法源缺条文",
    "no_numbers": "未标条号",
    "no_source": "未挂法源",
}


def _parse(payload: str | None) -> dict | None:
    if not payload:
        return None
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _parse_list(payload: str | None) -> list[dict]:
    if not payload:
        return []
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _rate(numerator: int, denominator: int) -> float | None:
    """分母为 0 时返回 None（「没有可统计的样本」不等于 0%）。"""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _source_rows(db: Session, source_refs: dict[int, dict]) -> list[dict]:
    """把引用计数补上法源标题与条文数，按引用次数倒序（前端表格只有前 N 行）。"""
    if not source_refs:
        return []
    ids = list(source_refs)
    titles = {
        row.id: (row.title or row.citation or f"法源 #{row.id}")
        for row in db.query(LegalSource.id, LegalSource.title, LegalSource.citation)
        .filter(LegalSource.id.in_(ids))
        .all()
    }
    article_counts = dict(
        db.query(LegalArticle.source_id, func.count(LegalArticle.id))
        .filter(LegalArticle.source_id.in_(ids))
        .group_by(LegalArticle.source_id)
        .all()
    )
    rows = []
    for source_id, entry in source_refs.items():
        rows.append(
            {
                "source_id": source_id,
                "title": titles.get(source_id, f"法源 #{source_id}（已删除）"),
                "references": entry["references"],
                "consultations": entry["consultations"],
                "verified": entry["verified"],
                "no_corpus": entry["no_corpus"],
                "articles": int(article_counts.get(source_id, 0)),
            }
        )
    rows.sort(key=lambda row: (-row["references"], row["source_id"]))
    return rows


def _source_titles(db: Session, ids: set[int]) -> dict[int, dict]:
    if not ids:
        return {}
    return {
        row.id: {"title": row.title, "citation": row.citation}
        for row in db.query(LegalSource.id, LegalSource.title, LegalSource.citation)
        .filter(LegalSource.id.in_(ids))
        .all()
    }


class ConsultationEvidenceMixin:
    def get_consultation_evidence_stats(
        self,
        db: Session,
        *,
        days: int = 30,
        organization_id: int | None = None,
    ) -> dict:
        """近 N 天咨询答复的「证据可核验性」分布 + 趋势 + 法源引用/语料缺口。

        - `levels`：四档分布（`strong/partial/weak/none`）+ 占比，分母是**有 evidence 的答复数**；
        - `references`：引用条数与三态计数（逐条核对通过 / 条号未找到 / 法源无条文 / 未标条号 / 未挂法源）；
        - `checked_rate`：`article_verified / article_checked`——**只算真的做过条文级核对的引用**；
        - `evidence_coverage`：有 evidence 的答复 / 窗口内全部咨询——用来看指标本身的覆盖面；
          其中 `with_evidence_generated`（生成当时算的）与 `with_evidence_recomputed`
          （2026-10-19 之后按当前法源库回填的，见 `scripts/backfill_consultation_evidence.py`）
          分开计数：两者都算「有指标」，但只有前者是生成当时的事实；
        - `trend`：按天分桶的同口径计数（只有有数据的日期出现）；
        - `sources` / `corpus_gaps`：被引用的法源排行，以及其中**没有条文级数据**的那些
          （`articles == 0`）——后者就是语料采购清单；
        - `unmatched_references`：没挂到法源库的引用（没有 `source_id`），单独计数不摊进排行。
        """
        window = max(1, min(int(days or 30), 365))
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window)

        query = db.query(
            LegalConsultation.evidence_json,
            LegalConsultation.references_json,
            LegalConsultation.created_at,
        ).filter(LegalConsultation.created_at >= since)
        if organization_id is not None:
            query = query.join(User, User.id == LegalConsultation.user_id).filter(
                User.organization_id == organization_id
            )
        rows = query.all()

        levels = {level: 0 for level in LEVEL_LABELS}
        references = {key: 0 for key in _REFERENCE_KEYS}
        with_evidence = 0
        with_evidence_recomputed = 0
        trend: dict[str, dict] = {}
        source_refs: dict[int, dict] = {}
        unmatched_references = 0
        unmatched_consultations = 0

        for evidence_json, references_json, created_at in rows:
            summary = _parse(evidence_json)
            if summary is not None:
                with_evidence += 1
                recomputed = summary.get("basis") == RECOMPUTED_BASIS
                if recomputed:
                    with_evidence_recomputed += 1
                level = summary.get("level")
                if level in levels:
                    levels[level] += 1
                for key in _REFERENCE_KEYS:
                    value = summary.get(key)
                    if isinstance(value, int) and value > 0:
                        references[key] += value

            day = created_at.date().isoformat() if created_at else None
            if day:
                bucket = trend.setdefault(
                    day,
                    {
                        "date": day,
                        "total": 0,
                        "with_evidence": 0,
                        "with_evidence_recomputed": 0,
                        "article_verified": 0,
                        "article_checked": 0,
                        **{level: 0 for level in LEVEL_LABELS},
                    },
                )
                bucket["total"] += 1
                if summary is not None:
                    bucket["with_evidence"] += 1
                    if summary.get("basis") == RECOMPUTED_BASIS:
                        bucket["with_evidence_recomputed"] += 1
                    if level in LEVEL_LABELS:
                        bucket[level] += 1
                    for key in ("article_verified", "article_checked"):
                        value = summary.get(key)
                        if isinstance(value, int) and value > 0:
                            bucket[key] += value

            # 法源引用排行：只认挂了 source_id 的引用；没挂的单独计数
            unmatched_in_row = False
            sources_in_row: set[int] = set()
            for ref in _parse_list(references_json):
                source_id = ref.get("source_id")
                if not isinstance(source_id, int):
                    unmatched_references += 1
                    unmatched_in_row = True
                    continue
                entry = source_refs.setdefault(
                    source_id, {"references": 0, "consultations": 0, "verified": 0, "no_corpus": 0}
                )
                entry["references"] += 1
                sources_in_row.add(source_id)
                verification = ref.get("verification") if isinstance(ref.get("verification"), dict) else {}
                if verification.get("verified") is True:
                    entry["verified"] += 1
                if verification.get("article_scope") == "no_corpus":
                    entry["no_corpus"] += 1
            for source_id in sources_in_row:
                source_refs[source_id]["consultations"] += 1
            if unmatched_in_row:
                unmatched_consultations += 1

        sources = _source_rows(db, source_refs)
        # 缺口清单从**全量**排行里挑（不是 top N 的子集），否则冷门但零条文的法源会被漏掉
        gaps = [row for row in sources if not row["articles"]][:SOURCE_LIMIT]
        sources = sources[:SOURCE_LIMIT]

        total = len(rows)
        return {
            "window_days": window,
            "total_consultations": total,
            "with_evidence": with_evidence,
            "with_evidence_generated": with_evidence - with_evidence_recomputed,
            "with_evidence_recomputed": with_evidence_recomputed,
            "without_evidence": total - with_evidence,
            "evidence_coverage": _rate(with_evidence, total),
            "levels": [
                {
                    "level": level,
                    "label": LEVEL_LABELS[level],
                    "count": count,
                    "share": _rate(count, with_evidence),
                }
                for level, count in levels.items()
            ],
            "references": references,
            "checked_rate": _rate(references["article_verified"], references["article_checked"]),
            "trend": sorted(trend.values(), key=lambda row: row["date"]),
            "sources": sources,
            "sources_total": len(source_refs),
            "corpus_gaps": gaps,
            "unmatched_references": {
                "references": unmatched_references,
                "consultations": unmatched_consultations,
            },
        }

    def list_consultation_evidence_issues(
        self,
        db: Session,
        *,
        days: int = 30,
        organization_id: int | None = None,
        issue: str | None = None,
        source_id: int | None = None,
        limit: int = 50,
    ) -> dict:
        """引用质量问题清单（从聚合数字下钻到具体答复）。

        - 只列有 `evidence_json` 的答复，口径与 `get_consultation_evidence_stats` 一致；
        - `issue` 支持 missing / no_corpus / unmatched / weak；
        - `source_id` 用于「某部法源/语料缺口」的定向下钻，只保留引用过该法源的答复；
        - 返回每条答复的问题类型与最多 8 条引用明细，供管理员判断是补语料还是改引用。
        """
        if issue and issue not in ISSUE_LABELS:
            raise ValueError(f"未知的引用问题类型: {issue}")
        window = max(1, min(int(days or 30), 365))
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window)
        query = (
            db.query(
                LegalConsultation.id,
                LegalConsultation.user_id,
                User.username,
                LegalConsultation.question,
                LegalConsultation.category,
                LegalConsultation.status,
                LegalConsultation.created_at,
                LegalConsultation.evidence_json,
                LegalConsultation.references_json,
            )
            .join(User, User.id == LegalConsultation.user_id)
            .filter(LegalConsultation.created_at >= since)
        )
        if organization_id is not None:
            query = query.filter(User.organization_id == organization_id)
        rows = query.order_by(
            LegalConsultation.created_at.desc(), LegalConsultation.id.desc()
        ).all()

        records = []
        source_ids: set[int] = set()
        for (
            consultation_id,
            user_id,
            username,
            question,
            category,
            status,
            created_at,
            evidence_json,
            references_json,
        ) in rows:
            summary = _parse(evidence_json)
            if summary is None:
                continue
            level = summary.get("level")
            if level not in LEVEL_LABELS:
                continue
            refs = _parse_list(references_json)
            issues: set[str] = set()
            if level in ("weak", "none"):
                issues.add("weak")
            for ref in refs:
                ref_source_id = ref.get("source_id")
                if not isinstance(ref_source_id, int):
                    issues.add("unmatched")
                else:
                    source_ids.add(ref_source_id)
                verification = ref.get("verification") if isinstance(ref.get("verification"), dict) else {}
                scope = verification.get("article_scope")
                if scope == "no_corpus":
                    issues.add("no_corpus")
                elif scope == "missing":
                    issues.add("missing")
            records.append(
                {
                    "id": consultation_id,
                    "user_id": user_id,
                    "user_name": username,
                    "question": question,
                    "category": category,
                    "status": status,
                    "created_at": created_at,
                    "level": level,
                    "summary": summary,
                    "refs": refs,
                    "issues": issues,
                }
            )

        titles = _source_titles(db, source_ids)
        items = []
        for record in records:
            refs = record["refs"]
            matched_source = source_id is None or any(
                isinstance(ref.get("source_id"), int) and ref.get("source_id") == source_id
                for ref in refs
            )
            if not matched_source:
                continue
            if issue:
                matched_issue = issue in record["issues"]
                if not matched_issue:
                    continue
            elif source_id is None and not record["issues"]:
                # 默认只列有问题的答复；点某部法源时允许看到该法源的全部引用（含已核对）
                continue

            details = []
            for ref in refs:
                ref_source_id = ref.get("source_id") if isinstance(ref.get("source_id"), int) else None
                if source_id is not None and ref_source_id != source_id:
                    continue
                verification = ref.get("verification") if isinstance(ref.get("verification"), dict) else {}
                scope = verification.get("article_scope") or (
                    "no_source" if ref_source_id is None else None
                )
                source_meta = titles.get(ref_source_id) or {}
                details.append(
                    {
                        "source_id": ref_source_id,
                        "title": ref.get("title") or source_meta.get("title") or "未挂法源",
                        "citation": ref.get("citation") or "",
                        "scope": scope,
                        "scope_label": SCOPE_LABELS.get(scope, "未核对"),
                        "verified": verification.get("verified"),
                        "note": verification.get("verification_note") or "",
                    }
                )
                if len(details) >= 8:
                    break

            issues = [key for key in ISSUE_ORDER if key in record["issues"]]
            items.append(
                {
                    "id": record["id"],
                    "user_id": record["user_id"],
                    "user_name": record["user_name"],
                    "question": record["question"],
                    "category": record["category"],
                    "status": record["status"],
                    "created_at": record["created_at"],
                    "level": record["level"],
                    "level_label": LEVEL_LABELS[record["level"]],
                    "basis": record["summary"].get("basis"),
                    "issues": issues,
                    "issue_labels": [ISSUE_LABELS[key] for key in issues],
                    "reference_count": len(refs),
                    "references": details,
                }
            )

        total = len(items)
        capped = max(1, min(int(limit or 50), 100))
        return {
            "window_days": window,
            "total": total,
            "returned": len(items[:capped]),
            "items": items[:capped],
        }
