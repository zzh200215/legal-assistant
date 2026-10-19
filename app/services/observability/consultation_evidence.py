"""证据可核验性在线指标的聚合（管理员观测用）。

数据源是 `legal_consultations.evidence_json`——生成时算好的确定性统计（见迁移
`20261019_0100` 与 `docs/review/step17-evidence-verifiability.md`）。这里只做聚合与
比率计算，不改写任何记录。

口径与前端一致：**没有 `evidence_json` 的老记录单独计数，不摊进比率分母**——
「没统计过」不等于「核验失败」。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.legal import LegalConsultation
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


def _parse(payload: str | None) -> dict | None:
    if not payload:
        return None
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _rate(numerator: int, denominator: int) -> float | None:
    """分母为 0 时返回 None（「没有可统计的样本」不等于 0%）。"""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


class ConsultationEvidenceMixin:
    def get_consultation_evidence_stats(
        self,
        db: Session,
        *,
        days: int = 30,
        organization_id: int | None = None,
    ) -> dict:
        """近 N 天咨询答复的「证据可核验性」分布。

        - `levels`：四档分布（`strong/partial/weak/none`）+ 占比，分母是**有 evidence 的答复数**；
        - `references`：引用条数与三态计数（逐条核对通过 / 条号未找到 / 法源无条文 / 未标条号 / 未挂法源）；
        - `checked_rate`：`article_verified / article_checked`——**只算真的做过条文级核对的引用**；
        - `evidence_coverage`：有 evidence 的答复 / 窗口内全部咨询——用来看指标本身的覆盖面。
        """
        window = max(1, min(int(days or 30), 365))
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window)

        query = db.query(LegalConsultation.evidence_json).filter(
            LegalConsultation.created_at >= since
        )
        if organization_id is not None:
            query = query.join(User, User.id == LegalConsultation.user_id).filter(
                User.organization_id == organization_id
            )
        rows = query.all()

        levels = {level: 0 for level in LEVEL_LABELS}
        references = {key: 0 for key in _REFERENCE_KEYS}
        with_evidence = 0
        for (payload,) in rows:
            summary = _parse(payload)
            if summary is None:
                continue
            with_evidence += 1
            level = summary.get("level")
            if level in levels:
                levels[level] += 1
            for key in _REFERENCE_KEYS:
                value = summary.get(key)
                if isinstance(value, int) and value > 0:
                    references[key] += value

        total = len(rows)
        return {
            "window_days": window,
            "total_consultations": total,
            "with_evidence": with_evidence,
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
        }
