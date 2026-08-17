"""合同条款级 Diff 服务

优先对 LegalContractClause 列表做条款级比对；
若任意版本无条款数据或置信度不足，降级为段落级比对并标记 needs_confirmation。
"""

import difflib
from typing import Optional

from sqlalchemy.orm import Session

from app.models.legal_contract import LegalContractClause, LegalContractVersion

_MIN_CLAUSE_CONFIDENCE = 0.7   # 低于此置信度降级
_MIN_CLAUSE_COUNT = 2           # 少于此条款数降级


def _clause_key(clause: LegalContractClause) -> str:
    return clause.clause_no or f"seq:{clause.sequence}"


def compute_diff(
    base_version_id: int,
    target_version_id: int,
    db: Session,
) -> dict:
    """
    返回结构：
    {
        "mode": "clause" | "paragraph",
        "needs_confirmation": bool,
        "base_confidence": float | None,
        "target_confidence": float | None,
        "diff": [
            {
                "status": "equal" | "added" | "removed" | "modified",
                "clause_no": str | None,
                "base_content": str | None,
                "target_content": str | None,
            },
            ...
        ],
    }
    """
    base_ver = db.query(LegalContractVersion).filter(
        LegalContractVersion.id == base_version_id
    ).first()
    target_ver = db.query(LegalContractVersion).filter(
        LegalContractVersion.id == target_version_id
    ).first()

    if not base_ver or not target_ver:
        return {"mode": "error", "needs_confirmation": True, "diff": [], "error": "版本不存在"}

    base_clauses = db.query(LegalContractClause).filter(
        LegalContractClause.contract_version_id == base_version_id
    ).order_by(LegalContractClause.sequence).all()

    target_clauses = db.query(LegalContractClause).filter(
        LegalContractClause.contract_version_id == target_version_id
    ).order_by(LegalContractClause.sequence).all()

    # 降级判断
    base_conf = float(base_ver.parse_confidence or 0)
    target_conf = float(target_ver.parse_confidence or 0)
    degraded = (
        len(base_clauses) < _MIN_CLAUSE_COUNT
        or len(target_clauses) < _MIN_CLAUSE_COUNT
        or base_conf < _MIN_CLAUSE_CONFIDENCE
        or target_conf < _MIN_CLAUSE_CONFIDENCE
    )

    if degraded:
        return _paragraph_diff(base_ver, target_ver, base_conf, target_conf)

    return _clause_diff(base_clauses, target_clauses, base_conf, target_conf)


def _clause_diff(base_clauses, target_clauses, base_conf, target_conf) -> dict:
    base_map = {_clause_key(c): c for c in base_clauses}
    target_map = {_clause_key(c): c for c in target_clauses}

    all_keys = list(base_map) + [k for k in target_map if k not in base_map]
    diff = []
    for key in all_keys:
        b = base_map.get(key)
        t = target_map.get(key)
        if b and t:
            ratio = difflib.SequenceMatcher(None, b.content, t.content).ratio()
            status = "equal" if ratio > 0.98 else "modified"
        elif b:
            status = "removed"
        else:
            status = "added"

        diff.append({
            "status": status,
            "clause_no": key,
            "base_content": b.content if b else None,
            "target_content": t.content if t else None,
        })

    return {
        "mode": "clause",
        "needs_confirmation": False,
        "base_confidence": base_conf,
        "target_confidence": target_conf,
        "diff": diff,
    }


def _paragraph_diff(base_ver, target_ver, base_conf, target_conf) -> dict:
    base_text = base_ver.text_snapshot or ""
    target_text = target_ver.text_snapshot or ""

    base_paras = [p for p in base_text.split("\n\n") if p.strip()]
    target_paras = [p for p in target_text.split("\n\n") if p.strip()]

    matcher = difflib.SequenceMatcher(None, base_paras, target_paras)
    diff = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for p in base_paras[i1:i2]:
                diff.append({"status": "equal", "clause_no": None,
                              "base_content": p, "target_content": p})
        elif tag == "replace":
            for bp, tp in zip(base_paras[i1:i2], target_paras[j1:j2]):
                diff.append({"status": "modified", "clause_no": None,
                              "base_content": bp, "target_content": tp})
            for p in base_paras[i1 + min(i2 - i1, j2 - j1):i2]:
                diff.append({"status": "removed", "clause_no": None,
                              "base_content": p, "target_content": None})
            for p in target_paras[j1 + min(i2 - i1, j2 - j1):j2]:
                diff.append({"status": "added", "clause_no": None,
                              "base_content": None, "target_content": p})
        elif tag == "delete":
            for p in base_paras[i1:i2]:
                diff.append({"status": "removed", "clause_no": None,
                              "base_content": p, "target_content": None})
        elif tag == "insert":
            for p in target_paras[j1:j2]:
                diff.append({"status": "added", "clause_no": None,
                              "base_content": None, "target_content": p})

    return {
        "mode": "paragraph",
        "needs_confirmation": True,
        "base_confidence": base_conf,
        "target_confidence": target_conf,
        "diff": diff,
        "notice": "条款结构解析置信度不足，已降级为段落级比对，请人工确认",
    }


contract_diff_service = type("_Svc", (), {
    "compute_diff": staticmethod(compute_diff),
})()
