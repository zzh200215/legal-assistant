"""法条引用核验与版本效力（AI-4）。

对 AI 答案中的每一条法源引用做确定性、DB 派生的核验：
  - 效力状态：现行有效 / 待更新 / 已废止
  - 版本效力：是否被后续法规修订（amended_by_json 关联），引用时需核对现行版本
核验结果不信任 LLM 输出，一律基于法源库当前数据计算，可离线确定性测试。
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models.legal import LegalSource

# 效力状态展示文案
_STATUS_NOTES = {
    "active": "现行有效",
    "pending_update": "待更新，引用时需人工复核",
    "inactive": "已废止，不得作为法律依据",
}
_UNVERIFIED_NOTE = "未关联法源库条目，无法核验"


def _json_ids(raw: str | None) -> list[int]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [int(item) for item in value if str(item).isdigit()]


def verify_source(source: LegalSource, db: Session | None = None) -> dict:
    """对单个法源计算引用核验信息（版本 + 效力状态 + 修订链）。

    db 为 None 时无法解析修订方的标题，仅按是否存在修订关系标记 superseded。
    """
    amended_ids = _json_ids(source.amended_by_json)

    amended_by: list[dict] = []
    if amended_ids and db is not None:
        rows = (
            db.query(LegalSource)
            .filter(LegalSource.id.in_(amended_ids))
            .all()
        )
        by_id = {row.id: row for row in rows}
        for rid in amended_ids:
            row = by_id.get(rid)
            if row:
                amended_by.append({
                    "source_id": row.id,
                    "title": row.title,
                    "version": row.version,
                    "status": row.status,
                    "effective_date": str(row.effective_date) if row.effective_date else None,
                })

    superseded = bool(amended_by) or bool(amended_ids and db is None)
    active = source.status == "active"
    current_effective = active and not superseded

    if source.status == "inactive":
        note = _STATUS_NOTES["inactive"]
    elif source.status == "pending_update":
        note = _STATUS_NOTES["pending_update"]
    elif amended_by:
        titles = "、".join(f"《{item['title']}》" for item in amended_by[:3])
        note = f"已被{titles}修订，引用时注意核对现行版本"
    elif amended_ids:
        note = "已被后续法规修订，引用时注意核对现行版本"
    else:
        note = _STATUS_NOTES["active"]

    return {
        "source_id": source.id,
        "version": source.version,
        "status": source.status,
        "effective_date": str(source.effective_date) if source.effective_date else None,
        "current_effective": current_effective,
        "superseded": superseded,
        "amended_by": amended_by,
        "verification_note": note,
    }


def enrich_references(db: Session | None, refs: list[dict]) -> list[dict]:
    """为引用列表批量附加 verification（DB 派生，覆盖任何 LLM 输出）。

    db 为 None（无库环境，如评测/单测）时不附加 verification，原样返回。
    """
    if not refs or db is None:
        return refs
    ids = [ref.get("source_id") for ref in refs if ref.get("source_id")]
    by_id: dict[int, LegalSource] = {}
    if ids:
        rows = db.query(LegalSource).filter(LegalSource.id.in_(ids)).all()
        by_id = {row.id: row for row in rows}

    enriched = []
    for ref in refs:
        out = dict(ref)
        source = by_id.get(ref.get("source_id")) if ref.get("source_id") else None
        if source is None:
            out["verification"] = {"verified": False, "verification_note": _UNVERIFIED_NOTE}
        else:
            out["verification"] = verify_source(source, db=db)
        enriched.append(out)
    return enriched
