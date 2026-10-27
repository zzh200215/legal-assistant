"""合同台账审查摘要（物化）：刷新与计数。

台账列表过去在内存里做「版本 → 审查 → JSON 计数」，最远只能翻 1000 条。
现在把这段派生结果落成 ``legal_contract_review_summaries``，列表查询直接 JOIN 过滤。

口径（与 step36 读时派生一致）：
- 「最近一次审查」= 该合同**版本号最大**且带 ``source_review_id`` 的版本所指向的审查；
- ``risks_high``：``risk_level`` 为 high/critical；
- ``risks_unanchored``：``source_location.paragraph`` 为空的项（step25 的定位口径）；
- 没有审查 → 删除摘要行，台账显示「未审查」。

刷新时机（所有会改变上述派生值的写入路径）：
1. 创建带 ``source_review_id`` 的合同版本；
2. 审查状态流转（通过/退回/拒收等）；
3. 风险处置（接受/缓释/忽略/撤销）与就地补定位（改 ``source_location``）。
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models.legal import ContractReview
from app.models.legal_contract import (
    LegalContract,
    LegalContractReviewSummary,
    LegalContractVersion,
)


def review_counts(risks_json: str | None) -> dict[str, int]:
    """从 ``risks_json`` 统计总数 / 高风险 / 无定位（无定位口径同 step25）。"""
    try:
        risks = json.loads(risks_json or "[]")
    except (TypeError, ValueError):
        risks = []
    risks = [risk for risk in risks if isinstance(risk, dict)]
    high = sum(1 for risk in risks if risk.get("risk_level") in ("high", "critical"))
    unanchored = sum(
        1 for risk in risks if not (risk.get("source_location") or {}).get("paragraph")
    )
    return {"total": len(risks), "high": high, "unanchored": unanchored}


def latest_review_for_contract(db: Session, contract_id: int) -> ContractReview | None:
    """取合同最新带审查来源的版本所指向的审查；没有则返回 None。"""
    version = (
        db.query(LegalContractVersion)
        .filter(
            LegalContractVersion.contract_id == contract_id,
            LegalContractVersion.source_review_id.isnot(None),
        )
        .order_by(LegalContractVersion.version_no.desc(), LegalContractVersion.id.desc())
        .first()
    )
    if version is None:
        return None
    return db.get(ContractReview, version.source_review_id)


def refresh_contract_review_summary(
    db: Session, contract_id: int,
) -> LegalContractReviewSummary | None:
    """按当前数据重算并写入摘要行；没有审查则删除该行。幂等，调用方负责 commit。"""
    contract = db.get(LegalContract, contract_id)
    if contract is None:
        return None
    summary = (
        db.query(LegalContractReviewSummary)
        .filter(LegalContractReviewSummary.contract_id == contract_id)
        .first()
    )
    review = latest_review_for_contract(db, contract_id)
    if review is None:
        if summary is not None:
            db.delete(summary)
            db.flush()
        return None

    counts = review_counts(review.risks_json)
    if summary is None:
        summary = LegalContractReviewSummary(
            contract_id=contract.id, organization_id=contract.organization_id,
        )
        db.add(summary)
    summary.organization_id = contract.organization_id
    summary.review_id = review.id
    summary.review_status = review.status
    summary.review_version = review.version
    summary.risks_total = counts["total"]
    summary.risks_high = counts["high"]
    summary.risks_unanchored = counts["unanchored"]
    summary.reviewed_at = review.reviewed_at
    summary.review_updated_at = review.updated_at
    db.flush()
    return summary


def refresh_summaries_for_review(db: Session, review_id: int) -> int:
    """审查变化后刷新所有引用它的合同（正常是 0 或 1 个），返回刷新数。"""
    rows = (
        db.query(LegalContractVersion.contract_id)
        .filter(LegalContractVersion.source_review_id == review_id)
        .distinct()
        .all()
    )
    contract_ids = {contract_id for (contract_id,) in rows}
    for contract_id in contract_ids:
        refresh_contract_review_summary(db, contract_id)
    return len(contract_ids)


def backfill_contract_summaries(db: Session, *, batch_size: int = 200) -> int:
    """按合同重算一遍摘要（迁移/运维修数用）；返回处理合同数。"""
    last_id = 0
    processed = 0
    while True:
        rows = (
            db.query(LegalContract.id)
            .filter(LegalContract.id > last_id)
            .order_by(LegalContract.id.asc())
            .limit(batch_size)
            .all()
        )
        if not rows:
            break
        for (contract_id,) in rows:
            refresh_contract_review_summary(db, contract_id)
            last_id = contract_id
            processed += 1
        db.commit()
    return processed
