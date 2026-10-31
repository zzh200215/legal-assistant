"""文档与案件的关联读取（只依赖 documents.metadata_json 里的 case_id）。"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.legal import LegalCase


def parse_case_id(metadata_json: str | None) -> int | None:
    """从文档 metadata 里取 case_id，兼容 int / 数字字符串；非法值一律 None。"""
    if not metadata_json:
        return None
    try:
        metadata = json.loads(metadata_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("case_id")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        case_id = int(raw)
    except (TypeError, ValueError):
        return None
    return case_id if case_id > 0 else None


def document_case_id(db: Session, document_id: int) -> int | None:
    """文档归属的案件 id；案件已删除时返回 None，避免写入悬空 case_id。"""
    row = db.query(Document.metadata_json).filter(Document.id == document_id).first()
    if not row:
        return None
    case_id = parse_case_id(row[0])
    if not case_id:
        return None
    exists = db.query(LegalCase.id).filter(LegalCase.id == case_id).first()
    return case_id if exists else None
