"""合同列表查询服务（P1 API 统一化）：可见性过滤、审查筛选、排序与分页全部下沉 SQL。

- 可见性语义与 legal_contract_api.list_contracts 原实现一致：
  无案件合同 → 组织成员可见；普通案件合同 → 组织成员可见；
  严格案件（is_strict_mode=1）合同 → 仅未撤销案件成员可见。
  案件成员判断用 EXISTS 子查询（不用 JOIN），避免一个用户在同一案件有多条成员记录时
  把合同行放大、也避免 DISTINCT 与「按审查时间排序」在 MySQL 上冲突。
- 审查/风险筛选走物化摘要表 ``legal_contract_review_summaries``（step44）：
  JOIN + WHERE + offset/limit + count，不再有 1000 条上限；
- 排序支持合同更新时间（默认）与最近审查时间；未审查（NULL）始终排在最后，
  同一时间按合同 id 倒序保证分页稳定；
- items 为显式序列化 dict，不返回合同正文（description 为加密列，禁止外泄）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from app.models.legal import LegalCase
from app.models.legal_contract import (
    LegalContract,
    LegalContractMilestone,
    LegalContractReviewSummary,
)

# 台账排序白名单：只允许按白名单列排，避免把任意列名拼进 SQL。
SORT_COLUMNS = {
    "updated_at": LegalContract.updated_at,
    "created_at": LegalContract.created_at,
    "reviewed_at": LegalContractReviewSummary.reviewed_at,
    "review_updated_at": LegalContractReviewSummary.review_updated_at,
}
DEFAULT_SORT = "updated_at"

# 风险筛选白名单：列 > 0 即命中；``none`` 单独表示「没有审查摘要」。
RISK_COLUMNS = {
    "critical": LegalContractReviewSummary.risks_critical,
    "high": LegalContractReviewSummary.risks_high,
    "medium": LegalContractReviewSummary.risks_medium,
    "low": LegalContractReviewSummary.risks_low,
    "unanchored": LegalContractReviewSummary.risks_unanchored,
}


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


def serialize_review_summary(summary: LegalContractReviewSummary | None) -> dict[str, Any] | None:
    """台账行里的「最近一次审查」：字段与 step36 读时派生保持一致。"""
    if summary is None:
        return None
    return {
        "id": summary.review_id,
        "status": summary.review_status,
        "version": summary.review_version,
        "risks_total": summary.risks_total or 0,
        "risks_high": summary.risks_high or 0,
        "risks_critical": summary.risks_critical or 0,
        "risks_medium": summary.risks_medium or 0,
        "risks_low": summary.risks_low or 0,
        "risks_unanchored": summary.risks_unanchored or 0,
        "reviewed_at": summary.reviewed_at,
        "updated_at": summary.review_updated_at,
    }


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
    sort: str = DEFAULT_SORT,
    order: str = "desc",
) -> dict:
    from app.models.legal_portal import LegalCaseMember

    member_exists = select(LegalCaseMember.id).where(
        LegalCaseMember.case_id == LegalContract.case_id,
        LegalCaseMember.user_id == user_id,
        LegalCaseMember.revoked_at.is_(None),
    ).correlate(LegalContract).exists()

    query = (
        db.query(LegalContract, LegalContractReviewSummary)
        .outerjoin(
            LegalContractReviewSummary,
            LegalContractReviewSummary.contract_id == LegalContract.id,
        )
        .filter(LegalContract.organization_id == org_id)
        .outerjoin(LegalCase, LegalContract.case_id == LegalCase.id)
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
        member_exists,
    ))
    # 审查/风险筛选下沉 SQL：摘要表 1:1 JOIN，可直接 WHERE + 分页
    if review_status:
        query = query.filter(LegalContractReviewSummary.review_status == review_status)
    if risk == "none":
        query = query.filter(LegalContractReviewSummary.contract_id.is_(None))
    elif risk in RISK_COLUMNS:
        query = query.filter(RISK_COLUMNS[risk] > 0)

    sort_column = SORT_COLUMNS.get(sort, SORT_COLUMNS[DEFAULT_SORT])
    descending = str(order or "desc").lower() != "asc"
    # 未审查的 reviewed_at 为 NULL：无论升降序都排最后，避免空值混在有效时间中间。
    nulls_last = case((sort_column.is_(None), 1), else_=0)
    direction = sort_column.desc() if descending else sort_column.asc()

    total = query.count()
    rows = (
        query
        .order_by(nulls_last, direction, LegalContract.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = []
    for contract, summary in rows:
        item = serialize_contract(contract)
        item["review"] = serialize_review_summary(summary)
        items.append(item)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort": sort if sort in SORT_COLUMNS else DEFAULT_SORT,
        "order": "desc" if descending else "asc",
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
