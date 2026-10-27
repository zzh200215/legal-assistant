"""合同列表查询服务（P1 API 统一化）：可见性过滤、审查筛选、排序与游标分页全部下沉 SQL。

- 可见性语义与 legal_contract_api.list_contracts 原实现一致：
  无案件合同 → 组织成员可见；普通案件合同 → 组织成员可见；
  严格案件（is_strict_mode=1）合同 → 仅未撤销案件成员可见。
  案件成员判断用 EXISTS 子查询（不用 JOIN），避免一个用户在同一案件有多条成员记录时
  把合同行放大。
- 审查/风险筛选走物化摘要表 ``legal_contract_review_summaries``（step44）：
  JOIN + WHERE + count，不再有 1000 条上限。
- 分页改为 **keyset（游标）**（step46）：排序键 + 合同 id 作为游标，不再用 OFFSET，
  翻页期间新增/删除不会造成重复或跳条；总数沿用第一页的快照，不每页重算。
- 排序支持合同更新时间/创建时间、最近审查时间/审查更新时间；空值统一排最后，
  同一时间按 id 同方向兜底。
- items 为显式序列化 dict，不返回合同正文（description 为加密列，禁止外泄）。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, text
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
CONTRACT_SORTS = frozenset({"updated_at", "created_at"})
REVIEW_SORTS = frozenset({"reviewed_at", "review_updated_at"})
DEFAULT_SORT = "updated_at"

# 风险筛选白名单：列 > 0 即命中；``none`` 单独表示「没有审查摘要」。
RISK_COLUMNS = {
    "critical": LegalContractReviewSummary.risks_critical,
    "high": LegalContractReviewSummary.risks_high,
    "medium": LegalContractReviewSummary.risks_medium,
    "low": LegalContractReviewSummary.risks_low,
    "unanchored": LegalContractReviewSummary.risks_unanchored,
}

CURSOR_VERSION = 1
SEGMENT_VALUE = "value"  # 排序键非空的一段
SEGMENT_TAIL = "tail"  # 排序键为空的一段（统一排最后）


def _filters_fingerprint(
    *,
    org_id: int,
    user_id: int,
    case_id: int | None,
    status: str | None,
    q: str | None,
    review_status: str | None,
    risk: str | None,
) -> str:
    """把「会影响结果集合」的条件固化成短哈希，防止游标跨筛选复用。"""
    material = json.dumps(
        {
            "org": org_id,
            "user": user_id,
            "case": case_id,
            "status": status or "",
            "q": q or "",
            "review": review_status or "",
            "risk": risk or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def encode_contract_cursor(
    *,
    sort: str,
    order: str,
    segment: str,
    value: datetime | None,
    contract_id: int,
    seen: int,
    total: int,
    fingerprint: str,
) -> str:
    """生成不透明游标：排序键、合同 id、已返回数量与第一页总数都带上。"""
    payload = {
        "v": CURSOR_VERSION,
        "s": sort,
        "o": order,
        "g": segment,
        "k": value.isoformat() if isinstance(value, datetime) else None,
        "i": contract_id,
        "n": seen,
        "t": total,
        "f": fingerprint,
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_contract_cursor(
    raw: str,
    *,
    sort: str,
    order: str,
    fingerprint: str,
) -> dict[str, Any]:
    """解析并校验游标；任何不匹配都抛 ``ValueError``（API 转 400，提示重新加载）。"""
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise ValueError("分页游标无效，请刷新列表") from exc

    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise ValueError("分页游标版本不支持，请刷新列表")
    if payload.get("s") != sort or payload.get("o") != order:
        raise ValueError("分页游标与当前排序不匹配，请刷新列表")
    if payload.get("f") != fingerprint:
        raise ValueError("分页游标与当前筛选不匹配，请刷新列表")

    segment = payload.get("g")
    if segment not in (SEGMENT_VALUE, SEGMENT_TAIL):
        raise ValueError("分页游标无效，请刷新列表")
    try:
        contract_id = int(payload["i"])
        seen = int(payload["n"])
        total = int(payload["t"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("分页游标无效，请刷新列表") from exc
    if contract_id <= 0 or seen < 0 or total < 0:
        raise ValueError("分页游标无效，请刷新列表")

    value = payload.get("k")
    if segment == SEGMENT_TAIL:
        value = None
    else:
        if not isinstance(value, str) or not value:
            raise ValueError("分页游标无效，请刷新列表")
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("分页游标无效，请刷新列表") from exc
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)

    return {
        "segment": segment,
        "value": value,
        "contract_id": contract_id,
        "seen": seen,
        "total": total,
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


SEARCH_FIELD_NAMES = ("title", "counterparty", "contract_no")


def search_hit(contract: LegalContract, q: str | None) -> dict[str, Any] | None:
    """给前端做「命中字段提示」：按字面子串标注命中了名称/相对方/编号。

    这是展示口径，不参与搜索判定（MySQL 的 ngram 短语匹配可能命中分词边界，
    此时列表仍返回该行，但这里可能标不出字段，前端会显示「分词匹配」）。
    """
    keyword = " ".join(str(q or "").split())
    if not keyword:
        return None
    needle = keyword.casefold()
    matched = []
    for field in SEARCH_FIELD_NAMES:
        value = getattr(contract, field, None)
        if value and needle in str(value).casefold():
            matched.append(field)
    return {"query": keyword, "matched_fields": matched}


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


def _member_exists(user_id: int):
    from app.models.legal_portal import LegalCaseMember

    return select(LegalCaseMember.id).where(
        LegalCaseMember.case_id == LegalContract.case_id,
        LegalCaseMember.user_id == user_id,
        LegalCaseMember.revoked_at.is_(None),
    ).correlate(LegalContract).exists()


def _contract_query(db: Session):
    """合同驱动：默认排序、空值尾段、以及输出行都用它。"""
    return (
        db.query(LegalContract, LegalContractReviewSummary)
        .outerjoin(
            LegalContractReviewSummary,
            LegalContractReviewSummary.contract_id == LegalContract.id,
        )
        .outerjoin(LegalCase, LegalContract.case_id == LegalCase.id)
    )


def _summary_query(db: Session):
    """摘要驱动：按审查时间排序时让 ``(organization_id, reviewed_at, contract_id)`` 索引生效。"""
    return (
        db.query(LegalContract, LegalContractReviewSummary)
        .select_from(LegalContractReviewSummary)
        .join(LegalContract, LegalContract.id == LegalContractReviewSummary.contract_id)
        .outerjoin(LegalCase, LegalContract.case_id == LegalCase.id)
    )


def _search_phrase(keyword: str) -> str:
    """把用户输入变成 MySQL 布尔模式的短语，避免 ``+ - * ( )`` 被当成操作符。"""
    escaped = keyword.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _search_filter(query, q: str):
    """MySQL 走 ngram 全文索引；其它方言（含 SQLite 开发库）回退三列 LIKE。

    ngram 的分词粒度默认 2，单字查询无法命中索引；这种情况同样回退 LIKE。
    """
    keyword = " ".join(str(q).split())
    if not keyword:
        return None
    dialect = query.session.get_bind().dialect.name
    if dialect == "mysql" and len(keyword) >= 2 and any(ch.isalnum() for ch in keyword):
        return text(
            "MATCH (legal_contracts.title, legal_contracts.counterparty, "
            "legal_contracts.contract_no) AGAINST (:contract_search IN BOOLEAN MODE)"
        ).bindparams(contract_search=_search_phrase(keyword))
    return or_(
        LegalContract.title.contains(keyword, autoescape=True),
        LegalContract.counterparty.contains(keyword, autoescape=True),
        LegalContract.contract_no.contains(keyword, autoescape=True),
    )


def _apply_filters(
    query,
    *,
    summary_driven: bool,
    org_id: int,
    user_id: int,
    case_id: int | None,
    status: str | None,
    q: str | None,
    review_status: str | None,
    risk: str | None,
):
    query = query.filter(LegalContract.organization_id == org_id)
    if summary_driven:
        # 与 JOIN 条件一起钉住索引前缀；摘要与合同的 org 正常一致。
        query = query.filter(LegalContractReviewSummary.organization_id == org_id)
    if case_id:
        query = query.filter(LegalContract.case_id == case_id)
    if status:
        query = query.filter(LegalContract.status == status)
    if q:
        condition = _search_filter(query, q)
        if condition is not None:
            query = query.filter(condition)
    # 可见性：无案件 / 案件非严格 / 严格案件成员
    query = query.filter(or_(
        LegalContract.case_id.is_(None),
        or_(LegalCase.is_strict_mode.is_(None), LegalCase.is_strict_mode == 0),
        _member_exists(user_id),
    ))
    if review_status:
        query = query.filter(LegalContractReviewSummary.review_status == review_status)
    if risk == "none":
        query = query.filter(LegalContractReviewSummary.contract_id.is_(None))
    elif risk in RISK_COLUMNS:
        query = query.filter(RISK_COLUMNS[risk] > 0)
    return query


def _order_by(column, id_column, descending: bool):
    if descending:
        return [column.desc(), id_column.desc()]
    return [column.asc(), id_column.asc()]


def _id_order(id_column, descending: bool):
    return [id_column.desc()] if descending else [id_column.asc()]


def _time_expression(db: Session, column):
    """SQLite 把时间存成两种文本（``CURRENT_TIMESTAMP`` 无小数、ORM 写 ``.000000``），
    直接比较字符串会把相等当成「小于」。开发/测试用的 SQLite 统一转 julianday 后再比；
    MySQL（生产）仍用原生 DATETIME，复合索引不受影响。
    """
    if db.get_bind().dialect.name == "sqlite":
        return func.julianday(column)
    return column


def _time_value(db: Session, value: datetime):
    if db.get_bind().dialect.name == "sqlite":
        return func.julianday(value)
    return value


def _keyset_predicate(column, id_column, *, value: Any, contract_id: int, descending: bool):
    """``(column, id) < (value, contract_id)`` 的展开写法，便于 MySQL 走复合索引范围。"""
    if descending:
        return or_(column < value, and_(column == value, id_column < contract_id))
    return or_(column > value, and_(column == value, id_column > contract_id))


def _id_predicate(id_column, contract_id: int, descending: bool):
    return id_column < contract_id if descending else id_column > contract_id


def _serialize_rows(rows, q: str | None = None) -> list[dict[str, Any]]:
    items = []
    for contract, summary in rows:
        item = serialize_contract(contract)
        item["review"] = serialize_review_summary(summary)
        item["search"] = search_hit(contract, q)
        items.append(item)
    return items


def list_visible_contracts(
    db: Session,
    *,
    org_id: int,
    user_id: int,
    case_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    page_size: int = 20,
    review_status: str | None = None,
    risk: str | None = None,
    sort: str = DEFAULT_SORT,
    order: str = "desc",
    cursor: str | None = None,
) -> dict:
    page_size = max(1, min(int(page_size), 100))
    sort = sort if sort in SORT_COLUMNS else DEFAULT_SORT
    descending = str(order or "desc").lower() != "asc"
    order = "desc" if descending else "asc"
    fingerprint = _filters_fingerprint(
        org_id=org_id, user_id=user_id, case_id=case_id, status=status,
        q=q, review_status=review_status, risk=risk,
    )
    state = decode_contract_cursor(
        cursor, sort=sort, order=order, fingerprint=fingerprint,
    ) if cursor else None

    filters = {
        "org_id": org_id,
        "user_id": user_id,
        "case_id": case_id,
        "status": status,
        "q": q,
        "review_status": review_status,
        "risk": risk,
    }
    total = state["total"] if state else _apply_filters(
        _contract_query(db), summary_driven=False, **filters,
    ).count()
    seen = state["seen"] if state else 0
    column = SORT_COLUMNS[sort]
    sort_expr = _time_expression(db, column)
    is_review_sort = sort in REVIEW_SORTS
    value_id = LegalContractReviewSummary.contract_id if is_review_sort else LegalContract.id

    if seen >= total:
        return {
            "items": [], "total": total, "page_size": page_size,
            "sort": sort, "order": order, "has_more": False, "next_cursor": None,
        }

    rows = []
    last_segment = SEGMENT_VALUE

    if state is None or state["segment"] == SEGMENT_VALUE:
        value_query = _apply_filters(
            _summary_query(db) if is_review_sort else _contract_query(db),
            summary_driven=is_review_sort,
            **filters,
        ).filter(column.isnot(None))
        if state:
            value_query = value_query.filter(_keyset_predicate(
                sort_expr, value_id,
                value=_time_value(db, state["value"]), contract_id=state["contract_id"],
                descending=descending,
            ))
        rows = (
            value_query.order_by(*_order_by(sort_expr, value_id, descending))
            .limit(page_size)
            .all()
        )
        if len(rows) < page_size:
            remaining = page_size - len(rows)
            tail_query = _apply_filters(
                _contract_query(db), summary_driven=False, **filters,
            ).filter(column.is_(None))
            tail_rows = (
                tail_query.order_by(*_id_order(LegalContract.id, descending))
                .limit(remaining)
                .all()
            )
            if tail_rows:
                rows.extend(tail_rows)
                last_segment = SEGMENT_TAIL
    else:
        tail_query = _apply_filters(
            _contract_query(db), summary_driven=False, **filters,
        ).filter(column.is_(None))
        tail_query = tail_query.filter(_id_predicate(
            LegalContract.id, state["contract_id"], descending,
        ))
        rows = (
            tail_query.order_by(*_id_order(LegalContract.id, descending))
            .limit(page_size)
            .all()
        )
        last_segment = SEGMENT_TAIL

    items = _serialize_rows(rows, q)
    loaded = seen + len(items)
    has_more = loaded < total and bool(items)

    next_cursor = None
    if has_more:
        contract, summary = rows[-1]
        if last_segment == SEGMENT_TAIL:
            segment = SEGMENT_TAIL
            value = None
            contract_id = contract.id
        else:
            segment = SEGMENT_VALUE
            value = getattr(summary, sort) if is_review_sort else getattr(contract, sort)
            contract_id = summary.contract_id if is_review_sort else contract.id
        next_cursor = encode_contract_cursor(
            sort=sort, order=order, segment=segment, value=value,
            contract_id=contract_id, seen=loaded, total=total,
            fingerprint=fingerprint,
        )

    return {
        "items": items,
        "total": total,
        "page_size": page_size,
        "sort": sort,
        "order": order,
        "has_more": has_more,
        "next_cursor": next_cursor,
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
