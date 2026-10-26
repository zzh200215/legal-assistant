"""合同列表查询服务（P1 API 统一化）：可见性过滤与分页全部下沉 SQL。

- 可见性语义与 legal_contract_api.list_contracts 原实现一致：
  无案件合同 → 组织成员可见；普通案件合同 → 组织成员可见；
  严格案件（is_strict_mode=1）合同 → 仅未撤销案件成员可见。
- 分页走 DB offset/limit + count（distinct 防多案件成员行重复）；
- items 为显式序列化 dict，不返回合同正文（description 为加密列，禁止外泄）。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.legal import ContractReview, LegalCase
from app.models.legal_contract import LegalContract, LegalContractMilestone, LegalContractVersion
from app.models.legal_portal import LegalCaseMember


def serialize_contract(contract: LegalContract) -> dict[str, Any]:
    """公开字段白名单：不含 description（加密合同正文）与内部列。"""
    return {
        "id": contract.id,
        "organization_id": contract.organization_id,
        "case_id": contract.case_id,
        "contract_no": contract.contract_no,
        "title": contract.title,
        "counterparty": contract.counterparty,
        "contract_type": contract.contract_type,
        "status": contract.status,
        "current_version_id": contract.current_version_id,
        "responsible_user_id": contract.responsible_user_id,
        "risk_level": contract.risk_level,
        "created_by": contract.created_by,
        "version": contract.version,
        "created_at": contract.created_at,
        "updated_at": contract.updated_at,
    }


def _review_summary(review: ContractReview) -> dict[str, Any]:
    """台账用的审查摘要：状态 + 风险数量 + 无定位数量（读 risks_json，不落库）。"""
    try:
        risks = json.loads(review.risks_json or "[]")
    except (TypeError, ValueError):
        risks = []
    risks = [risk for risk in risks if isinstance(risk, dict)]
    high = sum(1 for risk in risks if risk.get("risk_level") in ("high", "critical"))
    unanchored = sum(1 for risk in risks if not (risk.get("source_location") or {}).get("paragraph"))
    return {
        "id": review.id,
        "status": review.status,
        "version": review.version,
        "risks_total": len(risks),
        "risks_high": high,
        "risks_unanchored": unanchored,
        "reviewed_at": review.reviewed_at,
        "updated_at": review.updated_at,
    }


def _attach_latest_reviews(db: Session, contracts: list[LegalContract], items: list[dict]) -> None:
    """给台账行补「最近一次审查」：版本 -> source_review_id -> 审查摘要（批量查询，避免 N+1）。"""
    contract_ids = [contract.id for contract in contracts]
    if not contract_ids:
        return
    versions = (
        db.query(LegalContractVersion)
        .filter(
            LegalContractVersion.contract_id.in_(contract_ids),
            LegalContractVersion.source_review_id.isnot(None),
        )
        .order_by(LegalContractVersion.version_no.desc(), LegalContractVersion.id.desc())
        .all()
    )
    latest_by_contract: dict[int, LegalContractVersion] = {}
    for version in versions:
        latest_by_contract.setdefault(version.contract_id, version)
    review_ids = {version.source_review_id for version in latest_by_contract.values()}
    reviews = {
        review.id: review
        for review in db.query(ContractReview).filter(ContractReview.id.in_(review_ids)).all()
    } if review_ids else {}
    for contract, item in zip(contracts, items):
        version = latest_by_contract.get(contract.id)
        review = reviews.get(version.source_review_id) if version else None
        item["review"] = _review_summary(review) if review is not None else None


def serialize_milestone(milestone: LegalContractMilestone) -> dict[str, Any]:
    return {
        "id": milestone.id,
        "contract_id": milestone.contract_id,
        "contract_version_id": milestone.contract_version_id,
        "organization_id": milestone.organization_id,
        "milestone_type": milestone.milestone_type,
        "raw_text": milestone.raw_text,
        "standard_date": milestone.standard_date,
        "source_clause_no": milestone.source_clause_no,
        "confidence": milestone.confidence,
        "status": milestone.status,
        "confirmed_by": milestone.confirmed_by,
        "confirmed_at": milestone.confirmed_at,
        "note": milestone.note,
        "created_at": milestone.created_at,
        "updated_at": milestone.updated_at,
    }


def list_visible_contracts(
    db: Session,
    *,
    org_id: int,
    user_id: int,
    case_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
    review_status: str | None = None,
    risk: str | None = None,
) -> dict:
    query = (
        db.query(LegalContract)
        .filter(LegalContract.organization_id == org_id)
        .outerjoin(LegalCase, LegalContract.case_id == LegalCase.id)
        .outerjoin(
            LegalCaseMember,
            and_(
                LegalCaseMember.case_id == LegalContract.case_id,
                LegalCaseMember.user_id == user_id,
                LegalCaseMember.revoked_at.is_(None),
            ),
        )
    )
    if case_id:
        query = query.filter(LegalContract.case_id == case_id)
    if status:
        query = query.filter(LegalContract.status == status)
    if q:
        query = query.filter(LegalContract.title.contains(q))
    # 可见性：无案件 / 案件非严格 / 严格案件成员
    query = query.filter(or_(
        LegalContract.case_id.is_(None),
        or_(LegalCase.is_strict_mode.is_(None), LegalCase.is_strict_mode == 0),
        LegalCaseMember.id.isnot(None),
    ))
    ordered = query.distinct().order_by(LegalContract.updated_at.desc(), LegalContract.id.desc())
    if review_status or risk:
        # 审查/风险是派生字段（JSON + 版本关联），SQL 无法直接过滤：
        # 先取该组织可见合同（上限 1000，避免全表），再在内存里过滤 + 分页。
        rows = ordered.limit(1000).all()
        items = [serialize_contract(contract) for contract in rows]
        _attach_latest_reviews(db, rows, items)
        if review_status:
            items = [item for item in items if (item["review"] or {}).get("status") == review_status]
        if risk == "high":
            items = [item for item in items if (item["review"] or {}).get("risks_high", 0) > 0]
        elif risk == "unanchored":
            items = [item for item in items if (item["review"] or {}).get("risks_unanchored", 0) > 0]
        elif risk == "none":
            items = [item for item in items if item["review"] is None]
        total = len(items)
        start = (page - 1) * page_size
        return {
            "items": items[start:start + page_size],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    total = query.with_entities(LegalContract.id).distinct().count()
    rows = ordered.offset((page - 1) * page_size).limit(page_size).all()
    items = [serialize_contract(contract) for contract in rows]
    _attach_latest_reviews(db, rows, items)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def list_expiry_alerts(
    db: Session,
    *,
    org_id: int,
    page: int = 1,
    page_size: int = 20,
    review_status: str | None = None,
    risk: str | None = None,
) -> dict:
    query = (
        db.query(LegalContractMilestone)
        .filter(
            LegalContractMilestone.organization_id == org_id,
            LegalContractMilestone.milestone_type.in_(["expiry", "renewal"]),
            LegalContractMilestone.status == "confirmed",
        )
        .order_by(LegalContractMilestone.standard_date, LegalContractMilestone.id)
    )
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [serialize_milestone(milestone) for milestone in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
