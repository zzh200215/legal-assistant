"""P1 法律业务统一模型服务：结构化 Claim / Evidence / Reference / Fact / RiskItem。

设计：
- 工作台记录创建/重提时，把既有结构化输出（known/missing facts、risks、references、fields）
  确定性映射写入新结构化表（旁路写入；原 JSON 列继续保留为兼容快照与不可变原始输出）。
- 结论层级：legal_conclusion（须关联法源）/ risk_warning（须标注依据）/ fact_to_confirm（说明缺失原因）。
  无法可靠提供置信度时 confidence 保持 NULL，不伪造分数。
- 高风险风险项默认 needs_review；发布（final / publish）由 assert_publishable 强制校验。
- 风险项状态流转记录 reviewer / resolved_at / resolution_note，并同步关联的 risk_warning claim。
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.models.legal import (
    ContractReview,
    LegalCase,
    LegalConsultation,
    LegalDraft,
    LegalSource,
)
from app.models.legal_contract import LegalContractVersion
from app.models.legal_domain import (
    ContractRiskItem,
    LegalClaim,
    LegalEvidence,
    LegalFact,
    LegalReference,
)
from app.services.legal_reference_service import check_applicability

SEVERITIES = ("low", "medium", "high", "critical")
REVIEW_SEVERITIES = ("high", "critical")

# claim 生命周期：draft -> pending_review -> approved / changes_requested / rejected / unsupported / superseded
CLAIM_STATUS_OPEN = ("draft", "pending_review", "needs_review")
CLAIM_STATUS_RESOLVED = ("approved", "rejected", "unsupported", "superseded", "changes_requested")

# risk item 生命周期：open -> accepted / mitigated / dismissed；high/critical 创建即 needs_review
RISK_ITEM_ACTIONS = {"accept": "accepted", "mitigate": "mitigated", "dismiss": "dismissed"}
RISK_ITEM_RESOLVED = {"accepted", "mitigated", "dismissed"}


def _json_parse(raw: str | None, fallback):
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return fallback
    return value


def _org_for_case(db: Session, case_id: int | None) -> int | None:
    if not case_id:
        return None
    case = db.get(LegalCase, case_id)
    return case.organization_id if case else None


def _build_model_snapshot(db: Session, *, action: str, input_text: str) -> str:
    """原始模型结果快照：模型名、prompt 模板版本、输入 hash、生成时间。"""
    from app.core.config import get_settings

    settings = get_settings()
    snapshot = {
        "model": str(getattr(settings, "LLM_MODEL", None) or "unknown"),
        "action": action,
        "generated_at": utc_now().isoformat(),
        "input_sha256": hashlib.sha256((input_text or "").encode("utf-8")).hexdigest()[:32],
    }
    if db is not None:
        try:
            from app.models.prompt import PromptTemplate, PromptTemplateVersion

            tpl = db.query(PromptTemplate).filter(PromptTemplate.name == action).first()
            if tpl and tpl.active_version_id:
                ver = db.get(PromptTemplateVersion, tpl.active_version_id)
                if ver:
                    snapshot["prompt_version"] = ver.version
        except Exception:
            pass
    return json.dumps(snapshot, ensure_ascii=False)


def _add_reference(
    db: Session,
    ref: dict,
    *,
    org_id: int | None,
    user_id: int,
    case_id: int | None,
    claim_id: int | None = None,
) -> None:
    if not isinstance(ref, dict) or not ref.get("source_id"):
        return
    source_id = ref["source_id"]
    source = db.get(LegalSource, source_id)
    verdict = check_applicability(
        source, analysis_date=utc_now().date(), jurisdiction=ref.get("jurisdiction")
    ) if source is not None else {"applicable": None, "reason": "未关联法源库条目，适用性待核验"}
    snapshot = {
        k: ref.get(k) for k in ("version", "status", "effective_date")
        if ref.get(k) is not None
    }
    db.add(LegalReference(
        organization_id=org_id,
        user_id=user_id,
        case_id=case_id,
        claim_id=claim_id,
        source_id=source_id,
        article_number=ref.get("article_number"),
        citation_text=(ref.get("citation") or ref.get("title") or "")[:512] or None,
        analysis_date=utc_now(),
        jurisdiction=ref.get("jurisdiction"),
        applicable=verdict["applicable"],
        applicability_note=verdict.get("reason"),
        source_version_snapshot=json.dumps(snapshot, ensure_ascii=False)[:512] if snapshot else None,
    ))


# ── 持久化：确定性映射既有输出到结构化表 ───────────────────────────────────────

def persist_consultation_artifacts(
    db: Session,
    consultation: LegalConsultation,
    *,
    known: list[str],
    missing: list[str],
    refs: list[dict],
    risk_level: str,
) -> None:
    """咨询：facts（known/missing）+ references + 缺失事实的 fact_to_confirm claims。"""
    model_snapshot = _build_model_snapshot(db, action="legal_consultation", input_text=consultation.question)
    consultation.model_snapshot_json = model_snapshot
    org_id = _org_for_case(db, consultation.case_id)

    for fact in known or []:
        if isinstance(fact, str) and fact.strip():
            db.add(LegalFact(
                organization_id=org_id, user_id=consultation.user_id, case_id=consultation.case_id,
                consultation_id=consultation.id, fact_type="known", statement=fact, source="model",
            ))
    for fact in missing or []:
        if isinstance(fact, str) and fact.strip():
            db.add(LegalFact(
                organization_id=org_id, user_id=consultation.user_id, case_id=consultation.case_id,
                consultation_id=consultation.id, fact_type="missing", statement=fact, source="model",
            ))
    for ref in refs or []:
        _add_reference(db, ref, org_id=org_id, user_id=consultation.user_id, case_id=consultation.case_id)

    for fact in missing or []:
        if not isinstance(fact, str) or not fact.strip():
            continue
        db.add(LegalClaim(
            organization_id=org_id, user_id=consultation.user_id, case_id=consultation.case_id,
            source_type="consultation", source_id=consultation.id,
            claim_type="fact_to_confirm", title="待确认事实",
            summary=f"缺失事实：{fact}",
            statement=f"缺少事实「{fact}」，该信息影响结论可靠性，需补充后再确认法律结论。",
            status="pending_review", source="model", model_snapshot_json=model_snapshot,
            limitations_json=json.dumps(["信息不足，结论待补充事实后确认"], ensure_ascii=False),
        ))
    if risk_level == "high":
        db.add(LegalClaim(
            organization_id=org_id, user_id=consultation.user_id, case_id=consultation.case_id,
            source_type="consultation", source_id=consultation.id,
            claim_type="risk_warning", title="高风险提示",
            summary="系统评估存在高风险因素",
            statement="该事项存在较高法律风险，结果仅为风险提示，不等同于确定法律结论，须由律师复核。",
            status="needs_review", review_status="needs_review", source="model",
            model_snapshot_json=model_snapshot,
            limitations_json=json.dumps(["模型风险提示，未经律师审核确认"], ensure_ascii=False),
        ))
    db.commit()


def persist_review_artifacts(
    db: Session,
    review: ContractReview,
    *,
    risks: list[dict],
    refs: list[dict],
) -> None:
    """合同审查：risk items + references + 高/严重风险项的 risk_warning claims + evidences。"""
    model_snapshot = _build_model_snapshot(db, action="legal_contract_review", input_text=review.content)
    review.model_snapshot_json = model_snapshot
    org_id = _org_for_case(db, review.case_id)
    # 反向定位关联合同版本（LegalContractVersion.source_review_id -> review），供 Contract<->RiskItem 关联查询。
    contract_link = (
        db.query(LegalContractVersion)
        .filter(LegalContractVersion.source_review_id == review.id)
        .first()
    )
    contract_id = contract_link.contract_id if contract_link else None
    contract_version_id = contract_link.id if contract_link else None

    for item in risks or []:
        if not isinstance(item, dict):
            continue
        severity = item.get("risk_level")
        if severity not in SEVERITIES:
            severity = "medium"
        needs_review = severity in REVIEW_SEVERITIES
        status = "needs_review" if needs_review else "open"
        loc = item.get("source_location") or {}
        snippet = loc.get("snippet")
        risk_row = ContractRiskItem(
            organization_id=org_id, user_id=review.user_id, case_id=review.case_id,
            contract_id=contract_id, contract_version_id=contract_version_id,
            review_id=review.id, category=item.get("clause_type") or "other",
            severity=severity, title=item.get("label"), summary=item.get("description"),
            evidence_json=json.dumps(loc, ensure_ascii=False),
            recommendation=item.get("suggestion"),
            original_text_excerpt=str(snippet)[:1000] if snippet else None,
            source="model", status=status, review_status=status,
        )
        db.add(risk_row)
        db.flush()
        if needs_review:
            claim = LegalClaim(
                organization_id=org_id, user_id=review.user_id, case_id=review.case_id,
                source_type="contract_review", source_id=review.id, risk_item_id=risk_row.id,
                claim_type="risk_warning", title=item.get("label"),
                summary=item.get("description"), statement=item.get("description"),
                status="needs_review", review_status="needs_review", source="model",
                model_snapshot_json=model_snapshot,
                limitations_json=json.dumps(["模型风险提示，未经律师审核确认"], ensure_ascii=False),
            )
            db.add(claim)
            db.flush()
            if loc or snippet:
                db.add(LegalEvidence(
                    organization_id=org_id, user_id=review.user_id, case_id=review.case_id,
                    claim_id=claim.id, kind="support", description=item.get("description"),
                    extraction_method="model",
                    source_type="document" if loc.get("paragraph") else "clause",
                    loc_json=json.dumps(loc, ensure_ascii=False),
                    excerpt=str(snippet)[:1000] if snippet else None,
                ))
    for ref in refs or []:
        _add_reference(db, ref, org_id=org_id, user_id=review.user_id, case_id=review.case_id)
    db.commit()


def persist_draft_artifacts(
    db: Session,
    draft: LegalDraft,
    *,
    missing_fields: list[str],
    refs: list[dict],
) -> None:
    """文书草稿：references + 缺失关键字段的 fact_to_confirm claims。"""
    model_snapshot = _build_model_snapshot(
        db, action="legal_draft_generation", input_text=draft.fields_json or "{}",
    )
    draft.model_snapshot_json = model_snapshot
    org_id = _org_for_case(db, draft.case_id)
    for field in missing_fields or []:
        if not isinstance(field, str) or not field.strip():
            continue
        db.add(LegalClaim(
            organization_id=org_id, user_id=draft.user_id, case_id=draft.case_id,
            source_type="draft", source_id=draft.id,
            claim_type="fact_to_confirm", title="待补充字段",
            summary=f"缺少关键字段：{field}",
            statement=f"文书缺少关键字段「{field}」，该字段影响文书完整性与效力，需补充后重新审核。",
            status="pending_review", source="model", model_snapshot_json=model_snapshot,
            limitations_json=json.dumps(["字段缺失，草稿不完整"], ensure_ascii=False),
        ))
    for ref in refs or []:
        _add_reference(db, ref, org_id=org_id, user_id=draft.user_id, case_id=draft.case_id)
    db.commit()


# ── 查询聚合 ──────────────────────────────────────────────────────────────────

def serialize_risk_item(item: ContractRiskItem) -> dict:
    return {
        "id": item.id, "organization_id": item.organization_id, "case_id": item.case_id,
        "contract_id": item.contract_id, "contract_version_id": item.contract_version_id,
        "review_id": item.review_id, "clause_id": item.clause_id,
        "category": item.category, "severity": item.severity, "title": item.title,
        "summary": item.summary, "evidence": _json_parse(item.evidence_json, {}),
        "recommendation": item.recommendation, "suggested_revision": item.suggested_revision,
        "original_text_excerpt": item.original_text_excerpt,
        "legal_basis": _json_parse(item.legal_basis_json, []),
        "source": item.source, "status": item.status, "review_status": item.review_status,
        "reviewer_id": item.reviewer_id, "resolved_at": item.resolved_at,
        "resolution_note": item.resolution_note, "created_at": item.created_at,
    }


def serialize_claim(claim: LegalClaim) -> dict:
    return {
        "id": claim.id, "organization_id": claim.organization_id, "case_id": claim.case_id,
        "source_type": claim.source_type, "source_id": claim.source_id,
        "risk_item_id": claim.risk_item_id, "claim_type": claim.claim_type,
        "title": claim.title, "summary": claim.summary, "statement": claim.statement,
        "confidence": float(claim.confidence) if claim.confidence is not None else None,
        "assumptions": _json_parse(claim.assumptions_json, []),
        "limitations": _json_parse(claim.limitations_json, []),
        "status": claim.status, "review_status": claim.review_status,
        "source": claim.source, "model_snapshot": _json_parse(claim.model_snapshot_json, {}),
        "reviewer_id": claim.reviewer_id, "reviewed_at": claim.reviewed_at,
        "created_at": claim.created_at,
    }


def serialize_reference(ref: LegalReference) -> dict:
    return {
        "id": ref.id, "claim_id": ref.claim_id, "source_id": ref.source_id,
        "article_id": ref.article_id, "article_number": ref.article_number,
        "chapter": ref.chapter, "section": ref.section, "paragraph": ref.paragraph,
        "citation_text": ref.citation_text, "analysis_date": ref.analysis_date,
        "jurisdiction": ref.jurisdiction,
        "applicable": (True if ref.applicable == 1 else False if ref.applicable == 0 else None),
        "applicability_note": ref.applicability_note,
        "source_version_snapshot": _json_parse(ref.source_version_snapshot, {}),
        "created_at": ref.created_at,
    }


def serialize_evidence(evidence: LegalEvidence) -> dict:
    return {
        "id": evidence.id, "claim_id": evidence.claim_id, "fact_id": evidence.fact_id,
        "kind": evidence.kind, "description": evidence.description,
        "extraction_method": evidence.extraction_method, "source_type": evidence.source_type,
        "document_id": evidence.document_id, "contract_clause_id": evidence.contract_clause_id,
        "source_id": evidence.source_id, "article_id": evidence.article_id,
        "loc": _json_parse(evidence.loc_json, {}),
        "content_hash": evidence.content_hash, "excerpt": evidence.excerpt,
        "created_at": evidence.created_at,
    }


def get_case_domain(db: Session, case_id: int) -> dict:
    """案件级聚合：facts / evidences / claims / references / risk_items（按 case 过滤）。

    访问权限由调用方（API 层）通过 verify_case_access 保证。
    """
    facts = db.query(LegalFact).filter(LegalFact.case_id == case_id).order_by(LegalFact.id.desc()).all()
    claims = db.query(LegalClaim).filter(LegalClaim.case_id == case_id).order_by(LegalClaim.id.desc()).all()
    evidences = (
        db.query(LegalEvidence).filter(LegalEvidence.case_id == case_id).order_by(LegalEvidence.id.desc()).all()
    )
    references = (
        db.query(LegalReference).filter(LegalReference.case_id == case_id).order_by(LegalReference.id.desc()).all()
    )
    risk_items = (
        db.query(ContractRiskItem).filter(ContractRiskItem.case_id == case_id).order_by(ContractRiskItem.id.desc()).all()
    )
    return {
        "case_id": case_id,
        "facts": [{
            "id": f.id, "fact_type": f.fact_type, "statement": f.statement,
            "source": f.source, "consultation_id": f.consultation_id, "created_at": f.created_at,
        } for f in facts],
        "claims": [serialize_claim(c) for c in claims],
        "evidences": [serialize_evidence(e) for e in evidences],
        "references": [serialize_reference(r) for r in references],
        "risk_items": [serialize_risk_item(r) for r in risk_items],
    }


def get_risk_items(db: Session, review_id: int) -> list[dict]:
    return [
        serialize_risk_item(item)
        for item in db.query(ContractRiskItem).filter(ContractRiskItem.review_id == review_id)
        .order_by(ContractRiskItem.severity.desc(), ContractRiskItem.id.asc()).all()
    ]


def get_claims_for_target(db: Session, source_type: str, source_id: int) -> list[dict]:
    return [
        serialize_claim(c)
        for c in db.query(LegalClaim).filter(
            LegalClaim.source_type == source_type, LegalClaim.source_id == source_id,
        ).order_by(LegalClaim.id.desc()).all()
    ]


# ── 审核状态机 ────────────────────────────────────────────────────────────────

def update_risk_item_status(db: Session, user, risk_item_id: int, action: str, note: str | None = None) -> dict:
    """风险项状态流转：accept/mitigate/dismiss。仅审核角色可调用（API 层校验）。"""
    item = db.get(ContractRiskItem, risk_item_id)
    if not item:
        raise LookupError("RISK_ITEM_NOT_FOUND")
    if action not in RISK_ITEM_ACTIONS:
        raise ValueError("RISK_ITEM_ACTION_INVALID")
    if item.status in RISK_ITEM_RESOLVED:
        raise ValueError("RISK_ITEM_ALREADY_RESOLVED")
    target = RISK_ITEM_ACTIONS[action]
    item.status = target
    item.review_status = target
    item.reviewer_id = user.id
    item.resolved_at = utc_now()
    item.resolution_note = note
    # 同步关联的 risk_warning claim（claim.risk_item_id -> risk item）。
    claim = (
        db.query(LegalClaim)
        .filter(LegalClaim.risk_item_id == item.id)
        .first()
    )
    if claim and claim.status not in CLAIM_STATUS_RESOLVED:
        claim.status = "approved"
        claim.review_status = None
        claim.reviewer_id = user.id
        claim.reviewed_at = utc_now()
    db.commit()
    db.refresh(item)
    return serialize_risk_item(item)


def supersede_artifacts(db: Session, user, source_type: str, source_id: int) -> None:
    """内容进入新版本时，把旧版本未决的 risk items / claims 标记为被取代。

    - contract_risk_items：open/needs_review -> dismissed（记操作者/原因）；
    - legal_claims：未决 claim -> superseded。
    使"审核通过后修改需重新审核"可被追溯，且旧版本风险项不再阻塞新版本发布。
    """
    items = (
        db.query(ContractRiskItem)
        .filter(
            ContractRiskItem.review_id == source_id,
            ContractRiskItem.status.in_(("open", "needs_review")),
        )
        .all()
    )
    for item in items:
        item.status = "dismissed"
        item.review_status = None
        item.reviewer_id = user.id
        item.resolved_at = utc_now()
        item.resolution_note = "内容已修改进入新版本，本风险项随旧版本被取代"
    claims = (
        db.query(LegalClaim)
        .filter(
            LegalClaim.source_type == source_type,
            LegalClaim.source_id == source_id,
            LegalClaim.status.in_(CLAIM_STATUS_OPEN),
        )
        .all()
    )
    for claim in claims:
        claim.status = "superseded"
        claim.review_status = None
        claim.reviewer_id = user.id
        claim.reviewed_at = utc_now()
    db.commit()


def assert_publishable(db: Session, user, target_type: str, target_id: int) -> dict:
    """发布门禁（服务端强制）：已审核通过 + 无未处理高/严重风险项 + 无法源依据的法律结论。

    返回 {"ok": bool, "reasons": [str]}。reasons 非空即不可发布，调用方应拒绝操作。
    """
    reasons: list[str] = []
    row = None
    if target_type == "contract_review":
        row = db.get(ContractReview, target_id)
    elif target_type == "draft":
        row = db.get(LegalDraft, target_id)
    if row is None:
        return {"ok": False, "reasons": ["LEGAL_REVIEW_TARGET_NOT_FOUND"]}
    if row.status != "lawyer_approved":
        reasons.append("当前版本未审核通过（需 lawyer_approved），不可发布")
    if row.reviewed_version is not None and row.reviewed_version != row.version:
        reasons.append("内容自审核通过后已修改，需重新审核后再发布")

    if target_type == "contract_review":
        unresolved = (
            db.query(ContractRiskItem)
            .filter(
                ContractRiskItem.review_id == target_id,
                ContractRiskItem.severity.in_(REVIEW_SEVERITIES),
                ContractRiskItem.status.in_(("open", "needs_review")),
            )
            .count()
        )
        if unresolved:
            reasons.append(f"存在 {unresolved} 项未处理的高/严重风险项，不可发布")

    unsupported = (
        db.query(LegalClaim)
        .filter(
            LegalClaim.source_type == target_type,
            LegalClaim.source_id == target_id,
            LegalClaim.claim_type == "legal_conclusion",
            LegalClaim.status == "unsupported",
        )
        .count()
    )
    if unsupported:
        reasons.append(f"存在 {unsupported} 项无法源依据的法律结论，不可发布")
    return {"ok": not reasons, "reasons": reasons}


class LegalDomainService:
    """领域服务单例封装（便于测试替换/补丁）。"""

    persist_consultation_artifacts = staticmethod(persist_consultation_artifacts)
    persist_review_artifacts = staticmethod(persist_review_artifacts)
    persist_draft_artifacts = staticmethod(persist_draft_artifacts)
    get_case_domain = staticmethod(get_case_domain)
    get_risk_items = staticmethod(get_risk_items)
    get_claims_for_target = staticmethod(get_claims_for_target)
    update_risk_item_status = staticmethod(update_risk_item_status)
    supersede_artifacts = staticmethod(supersede_artifacts)
    assert_publishable = staticmethod(assert_publishable)


legal_domain_service = LegalDomainService()
