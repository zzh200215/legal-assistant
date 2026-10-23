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

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.legal import (
    ContractReview,
    LegalCase,
    LegalConsultation,
    LegalDraft,
    LegalReviewAction,
    LegalSource,
)
from app.models.legal_contract import LegalContractVersion
from app.models.user import User
from app.models.legal_domain import (
    LegalClaim,
    LegalEvidence,
    LegalFact,
    LegalReference,
)
from app.services.legal.legal_reference_service import check_applicability

SEVERITIES = ("low", "medium", "high", "critical")


def review_severities() -> tuple[str, ...]:
    """需强制进入审核队列的严重度（LEGAL_RISK_REVIEW_SEVERITIES，默认 high/critical）。

    创建侧（needs_review 初始状态）与发布门禁侧（未处理项统计）读同一处，
    避免改了配置只有一半生效。
    """
    return tuple(sorted(get_settings().legal_review_severity_set))

# claim 生命周期：draft -> pending_review -> approved / changes_requested / rejected / unsupported / superseded
CLAIM_STATUS_OPEN = ("draft", "pending_review", "needs_review")
CLAIM_STATUS_RESOLVED = ("approved", "rejected", "unsupported", "superseded", "changes_requested")

# risk item 生命周期：open -> accepted / mitigated / dismissed；high/critical 创建即 needs_review
RISK_ITEM_ACTIONS = {"accept": "accepted", "mitigate": "mitigated", "dismiss": "dismissed"}
RISK_ITEM_RESOLVED = {"accepted", "mitigated", "dismissed"}
RISK_ITEM_REOPEN = "reopen"


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


def finalize_consultation_trace(trace: dict | None, model_snapshot_json: str | None) -> str | None:
    """把模型名与提示词版本补进 trace 的「生成建议」步，返回可入库的 JSON。

    模型/提示词版本取自 model_snapshot（与响应里的 model_snapshot 同源），
    避免面板时间线与结果字段出现两套说法。没有 trace 时返回 None，调用方跳过写入。
    """
    if not isinstance(trace, dict) or not trace.get("steps"):
        return None
    snapshot: dict = {}
    if model_snapshot_json:
        try:
            parsed = json.loads(model_snapshot_json)
            if isinstance(parsed, dict):
                snapshot = parsed
        except (TypeError, ValueError):
            snapshot = {}
    model = str(snapshot.get("model") or "").strip()
    version = snapshot.get("prompt_version")
    suffix = " · ".join(
        part for part in (f"模型 {model}" if model else "", f"提示词 v{version}" if version else "") if part
    )
    if suffix:
        for step in trace["steps"]:
            if isinstance(step, dict) and step.get("id") == "generate":
                base = str(step.get("detail") or "").strip()
                step["detail"] = f"{base} · {suffix}" if base else suffix
    return json.dumps(trace, ensure_ascii=False)


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
    """合同审查：高风险项的 risk_warning claims + evidences + references。

    风险本身只存 `legal_contract_reviews.risks_json`（单一存储，见 20261022_0101 迁移）；
    这里只为需要律师复核的高/严重风险派生 claim 与 evidence（claim 通过 source_type/source_id
    归属审查记录，不再有 risk_item_id）。
    """
    model_snapshot = _build_model_snapshot(db, action="legal_contract_review", input_text=review.content)
    review.model_snapshot_json = model_snapshot
    # 单一存储：风险清单只落 risks_json（迁移 20261022_0101 删掉了 contract_risk_items）
    review.risks_json = json.dumps(risks or [], ensure_ascii=False)
    db.add(review)
    org_id = _org_for_case(db, review.case_id)

    for item in risks or []:
        if not isinstance(item, dict):
            continue
        severity = item.get("risk_level")
        if severity not in SEVERITIES:
            severity = "medium"
        if severity not in review_severities():
            continue
        loc = item.get("source_location") or {}
        snippet = loc.get("snippet")
        claim = LegalClaim(
            organization_id=org_id, user_id=review.user_id, case_id=review.case_id,
            source_type="contract_review", source_id=review.id,
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

def _apply_risk_transition(risk: dict, action: str, user, note: str | None) -> str:
    """校验并应用一次风险状态转换，返回目标状态（不改 DB）。"""
    current = risk_status(risk)
    severity = risk.get("risk_level") if risk.get("risk_level") in SEVERITIES else "medium"
    if action == RISK_ITEM_REOPEN:
        if current not in RISK_ITEM_RESOLVED:
            raise ValueError("RISK_ITEM_NOT_RESOLVED")
        # 撤销处置：回到待处理（高风险仍进审核队列），清空处置字段，历史保留在动作表
        target = "needs_review" if severity in review_severities() else "open"
        risk["reviewer_id"] = None
        risk["resolved_at"] = None
        risk["resolution_note"] = None
    else:
        if action not in RISK_ITEM_ACTIONS:
            raise ValueError("RISK_ITEM_ACTION_INVALID")
        if current in RISK_ITEM_RESOLVED:
            raise ValueError("RISK_ITEM_ALREADY_RESOLVED")
        target = RISK_ITEM_ACTIONS[action]
        risk["reviewer_id"] = user.id
        risk["resolved_at"] = utc_now().isoformat()
        risk["resolution_note"] = note
    risk["status"] = target
    return target


def _risk_action_history(db: Session, review_id: int) -> dict[int, list[dict]]:
    """该审查下按风险下标归组的处置动作（含操作人名），供风险项详情直接展示。"""
    rows = (
        db.query(LegalReviewAction, User)
        .outerjoin(User, User.id == LegalReviewAction.reviewer_id)
        .filter(
            LegalReviewAction.target_type == "contract_review",
            LegalReviewAction.target_id == review_id,
            LegalReviewAction.risk_index.isnot(None),
        )
        .order_by(LegalReviewAction.created_at.desc(), LegalReviewAction.id.desc())
        .all()
    )
    grouped: dict[int, list[dict]] = {}
    for action, actor in rows:
        grouped.setdefault(int(action.risk_index), []).append({
            "id": action.id,
            "action": action.action,
            "note": action.note,
            "from_status": action.from_status,
            "to_status": action.to_status,
            "risk_index": action.risk_index,
            "risk_title": action.risk_title,
            "reviewer_id": action.reviewer_id,
            "actor_name": (actor.full_name or actor.username) if actor else None,
            "created_at": action.created_at,
        })
    return grouped


def risk_status(risk: dict) -> str:
    """风险项状态：已处置（accepted/mitigated/dismissed）以写入为准，其余按严重度派生。

    高风险不允许停留在「open」——它是审核队列的入口（needs_review），旧数据里写成 open
    的也在这里归一，避免绕过发布门禁。
    """
    status = str(risk.get("status") or "").strip()
    if status in RISK_ITEM_RESOLVED:
        return status
    severity = risk.get("risk_level") if risk.get("risk_level") in SEVERITIES else "medium"
    return "needs_review" if severity in review_severities() else "open"


def serialize_risk_item(review: ContractReview, risk: dict, index: int) -> dict:
    """从 `risks_json` 的元素派生结构化风险项（单一存储，无独立表）。"""
    severity = risk.get("risk_level") if risk.get("risk_level") in SEVERITIES else "medium"
    location = risk.get("source_location") if isinstance(risk.get("source_location"), dict) else {}
    snippet = str(location.get("snippet") or "")[:1000] or None
    status = risk_status(risk)
    return {
        "id": index + 1, "index": index,
        "organization_id": None, "case_id": review.case_id,
        "contract_id": None, "contract_version_id": None,
        "review_id": review.id, "clause_id": None,
        "category": risk.get("clause_type") or "other", "severity": severity,
        "title": risk.get("label"), "summary": risk.get("description"),
        "evidence": location, "recommendation": risk.get("suggestion"),
        "suggested_revision": None, "original_text_excerpt": snippet,
        "legal_basis": risk.get("legal_basis") or [],
        "source": "model", "status": status, "review_status": status if status == "needs_review" else None,
        "reviewer_id": risk.get("reviewer_id"), "resolved_at": risk.get("resolved_at"),
        "resolution_note": risk.get("resolution_note"), "created_at": review.created_at,
    }


def serialize_claim(claim: LegalClaim) -> dict:
    return {
        "id": claim.id, "organization_id": claim.organization_id, "case_id": claim.case_id,
        "source_type": claim.source_type, "source_id": claim.source_id,
        "claim_type": claim.claim_type,
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
    risk_items = [
        item
        for review in db.query(ContractReview).filter(ContractReview.case_id == case_id).order_by(ContractReview.id.desc()).all()
        for item in get_risk_items(db, review.id, review=review)
    ]
    return {
        "case_id": case_id,
        "facts": [{
            "id": f.id, "fact_type": f.fact_type, "statement": f.statement,
            "source": f.source, "consultation_id": f.consultation_id, "created_at": f.created_at,
        } for f in facts],
        "claims": [serialize_claim(c) for c in claims],
        "evidences": [serialize_evidence(e) for e in evidences],
        "references": [serialize_reference(r) for r in references],
        "risk_items": risk_items,
    }


def get_risk_items(db: Session, review_id: int, review: ContractReview | None = None) -> list[dict]:
    """合同审查的结构化风险项：从 `risks_json` 派生（按下标稳定编号，1 基）。"""
    row = review or db.get(ContractReview, review_id)
    if row is None:
        return []
    risks = _json_parse(row.risks_json, [])
    history = _risk_action_history(db, row.id)
    items = []
    for index, risk in enumerate(risks):
        if not isinstance(risk, dict):
            continue
        item = serialize_risk_item(row, risk, index)
        item["history"] = history.get(index, [])
        items.append(item)
    return items


def get_claims_for_target(db: Session, source_type: str, source_id: int) -> list[dict]:
    return [
        serialize_claim(c)
        for c in db.query(LegalClaim).filter(
            LegalClaim.source_type == source_type, LegalClaim.source_id == source_id,
        ).order_by(LegalClaim.id.desc()).all()
    ]


# ── 审核状态机 ────────────────────────────────────────────────────────────────

def update_risk_item_status(
    db: Session, user, review_id: int, risk_index: int, action: str, note: str | None = None,
) -> dict:
    """风险项状态流转：accept/mitigate/dismiss，直接写回 `risks_json[risk_index]`。

    `risk_index` 是 0 基下标（API 暴露的 `id = index + 1`）。仅审核角色可调用（API 层校验）。
    """
    review = db.get(ContractReview, review_id)
    if review is None:
        raise LookupError("RISK_ITEM_NOT_FOUND")
    risks = _json_parse(review.risks_json, [])
    if not 0 <= risk_index < len(risks) or not isinstance(risks[risk_index], dict):
        raise LookupError("RISK_ITEM_NOT_FOUND")
    risk = risks[risk_index]
    current = risk_status(risk)
    target = _apply_risk_transition(risk, action, user, note)
    review.risks_json = json.dumps(risks, ensure_ascii=False)
    db.add(review)
    # 逐条追溯：处置动作落 legal_review_actions（带风险下标/标题快照）
    db.add(LegalReviewAction(
        reviewer_id=user.id,
        target_type="contract_review",
        target_id=review.id,
        action=f"risk_{action}",
        note=note,
        from_status=current,
        to_status=target,
        target_version=getattr(review, "version", None),
        risk_index=risk_index,
        risk_title=(str(risk.get("label"))[:256] if risk.get("label") else None),
    ))

    # 同步由该风险派生的 risk_warning claim（无外键：按 source + 标题/描述匹配）
    claim = (
        db.query(LegalClaim)
        .filter(
            LegalClaim.source_type == "contract_review",
            LegalClaim.source_id == review_id,
            LegalClaim.claim_type == "risk_warning",
            LegalClaim.title == risk.get("label"),
            LegalClaim.summary == risk.get("description"),
        )
        .first()
    )
    if claim and claim.status not in CLAIM_STATUS_RESOLVED:
        claim.status = "approved"
        claim.review_status = None
        claim.reviewer_id = user.id
        claim.reviewed_at = utc_now()
    db.commit()
    db.refresh(review)
    item = serialize_risk_item(review, risk, risk_index)
    item["history"] = _risk_action_history(db, review.id).get(risk_index, [])
    return item


def bulk_update_risk_item_status(
    db: Session, user, review_id: int, risk_indexes: list[int], action: str, note: str | None = None,
) -> list[dict]:
    """批量处置风险项：先整体校验（全有或全无），再逐条写回并各记一条动作。"""
    review = db.get(ContractReview, review_id)
    if review is None:
        raise LookupError("RISK_ITEM_NOT_FOUND")
    risks = _json_parse(review.risks_json, [])
    # 1) 全量校验：任何一条不合法都不改数据
    prepared: list[tuple[int, dict, str]] = []
    for index in risk_indexes:
        if not 0 <= index < len(risks) or not isinstance(risks[index], dict):
            raise LookupError("RISK_ITEM_NOT_FOUND")
        risk = risks[index]
        target = _apply_risk_transition(risk, action, user, note)  # 会就地改 risk，失败前已改则回滚
        prepared.append((index, risk, target))
    # 校验失败时可能已改过部分 risk：重新从 DB 读一遍覆盖，避免半改状态
    risks = _json_parse(review.risks_json, [])
    results = []
    for index, _risk, target in prepared:
        risk = risks[index]
        previous = risk_status(risk)
        target = _apply_risk_transition(risk, action, user, note)
        db.add(LegalReviewAction(
            reviewer_id=user.id, target_type="contract_review", target_id=review.id,
            action=f"risk_{action}", note=note, from_status=previous, to_status=target,
            target_version=getattr(review, "version", None), risk_index=index,
            risk_title=(str(risk.get("label"))[:256] if risk.get("label") else None),
        ))
        results.append((index, risk))
    review.risks_json = json.dumps(risks, ensure_ascii=False)
    db.add(review)
    db.commit()
    db.refresh(review)
    history = _risk_action_history(db, review.id)
    items = []
    for index, risk in results:
        item = serialize_risk_item(review, risk, index)
        item["history"] = history.get(index, [])
        items.append(item)
    return items


def supersede_artifacts(db: Session, user, source_type: str, source_id: int) -> None:
    """内容进入新版本时，把旧版本未决的 claims 标记为被取代。

    风险清单本身随新版本**整体替换**（risks_json 是单一存储），旧风险不再单独保留状态；
    可追溯性依赖审核动作日志与 claims：未决 claim -> superseded。
    """

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
        risks = _json_parse(row.risks_json, [])
        unresolved = sum(
            1
            for risk in risks
            if isinstance(risk, dict)
            and (risk.get("risk_level") if risk.get("risk_level") in SEVERITIES else "medium") in review_severities()
            and risk_status(risk) in ("open", "needs_review")
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
    bulk_update_risk_item_status = staticmethod(bulk_update_risk_item_status)
    supersede_artifacts = staticmethod(supersede_artifacts)
    assert_publishable = staticmethod(assert_publishable)


legal_domain_service = LegalDomainService()
