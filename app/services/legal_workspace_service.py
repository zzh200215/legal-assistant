"""Application module for the legal-workspace write workflows.

The HTTP layer should only translate requests and responses.  This module owns
the orchestration that is shared by the legal workspace endpoints: quota
checks, source selection, LLM execution, persistence, and audit records.
"""

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from fastapi import HTTPException

from app.core.auth import verify_case_access
from app.core.time import utc_now
from app.models.legal import (
    ContractReview,
    LegalConsultation,
    LegalDocumentVersion,
    LegalDraft,
    LegalReviewAction,
    LegalSource,
)
from app.models.legal_contract import LegalReviewPolicy, LegalReviewPolicyVersion
from app.models.user import User
from app.services.audit_log_service import AuditLogService
from app.services.legal_service import (
    DISCLAIMER,
    DRAFT_FIELDS,
    DRAFT_REQUIRED_FIELDS,
    compute_disclaimer_level,
    consultation_followup,
    consultation_payload,
    draft_content,
    ensure_demo_sources,
    ref_dict,
    review_contract,
    target_query,
)
from app.services.legal_reference_service import enrich_references
from app.services.subscription_service import subscription_service


DRAFT_TITLES = {
    "labor_arbitration_application": "劳动争议仲裁申请书",
    "private_lending_complaint": "民间借贷纠纷起诉状",
    "consumer_complaint": "消费纠纷投诉书",
    "supplementary_agreement": "补充协议",
}

WORKSPACE_TEMPLATE_LABELS = {
    "labor_arbitration_application": "劳动争议仲裁申请书",
    "private_lending_complaint": "民间借贷纠纷起诉状",
    "consumer_complaint": "消费纠纷投诉书",
    "supplementary_agreement": "补充协议",
}

REVIEWER_ACTIONS = {
    "approve": "lawyer_approved",
    "return": "returned_for_facts",
    "offline": "offline_consultation",
    "close": "archived",
}
OWNER_ACTIONS = {"submit_review": "needs_lawyer_review"}


def _json_or(value: str | None, fallback: str) -> object:
    try:
        return json.loads(value or fallback)
    except (TypeError, json.JSONDecodeError):
        return json.loads(fallback)


def compute_confidence(row: LegalConsultation | ContractReview | LegalDraft) -> int:
    """启发式置信度（0-100）：依据来源有效性、信息完备度、风险明确性推导。

    作为 AI 输出的信任指标（U-2），不落库，随每次序列化动态计算，
    因此历史记录同样可获得置信度。
    """
    if isinstance(row, ContractReview) or hasattr(row, "risks_json"):
        risks = _json_or(row.risks_json, "[]")
        located = sum(1 for r in risks if r.get("source_location"))
        needs_facts = sum(1 for r in risks if r.get("status") == "needs_facts")
        score = 55
        if risks:
            score += int(20 * min(located / len(risks), 1.0))
            score -= 10 * min(needs_facts, 3)
        return max(30, min(95, score))
    if isinstance(row, LegalDraft) or hasattr(row, "document_type"):
        missing = _json_or(row.missing_fields_json, "[]")
        return max(30, min(95, 90 - 15 * min(len(missing), 4)))
    refs = _json_or(row.references_json, "[]")
    missing = _json_or(row.missing_facts_json, "[]")
    score = 55
    if refs:
        score += 15
        active = sum(1 for r in refs if r.get("status") == "active")
        score += 5 if active else -10
    score -= 12 * min(len(missing), 3)
    if row.risk_level == "high":
        score -= 10
    return max(30, min(95, score))


def serialize_workspace_row(row: LegalConsultation | ContractReview | LegalDraft) -> dict:
    """Stable response representation shared by every workspace entry point."""
    if isinstance(row, LegalConsultation):
        return {
            "id": row.id, "case_id": row.case_id, "question": row.question, "category": row.category,
            "known_facts": _json_or(row.known_facts_json, "[]"),
            "missing_facts": _json_or(row.missing_facts_json, "[]"),
            "references": _json_or(row.references_json, "[]"), "advice": row.advice,
            "risk_level": row.risk_level, "status": row.status,
            "reviewer_id": row.reviewer_id, "review_note": row.review_note,
            "reviewed_at": row.reviewed_at,
            "confidence": compute_confidence(row),
            "feedback_score": row.feedback_score,
        }
    if isinstance(row, ContractReview):
        return {
            "id": row.id, "case_id": row.case_id, "title": row.title, "content": row.content,
            "document_id": row.document_id, "version": row.version, "status": row.status,
            "summary": row.summary, "risks": _json_or(row.risks_json, "[]"),
            "references": _json_or(row.references_json, "[]"),
            "review_policy_id": row.review_policy_id,
            "review_policy_version": row.review_policy_version,
            "review_policy_snapshot": _json_or(row.review_policy_snapshot_json, "{}"),
            "reviewer_id": row.reviewer_id, "review_note": row.review_note,
            "reviewed_at": row.reviewed_at,
            "confidence": compute_confidence(row),
            "feedback_score": row.feedback_score,
        }
    return {
        "id": row.id, "case_id": row.case_id, "document_type": row.document_type, "title": row.title,
        "fields": _json_or(row.fields_json, "{}"),
        "missing_fields": _json_or(row.missing_fields_json, "[]"),
        "references": _json_or(row.references_json, "[]"), "content": row.content,
        "version": row.version, "status": row.status, "reviewer_id": row.reviewer_id,
        "review_note": row.review_note, "reviewed_at": row.reviewed_at,
        "confidence": compute_confidence(row),
        "feedback_score": row.feedback_score,
    }


def serialize_workspace_version(version: LegalDocumentVersion) -> dict:
    return {
        "id": version.id, "target_type": version.target_type, "target_id": version.target_id,
        "version": version.version, "title": version.title, "content": version.content,
        "status_at_snapshot": version.status_at_snapshot,
        "snapshot_reason": version.snapshot_reason, "created_by": version.created_by,
        "created_at": version.created_at,
    }


def serialize_review_action(action: LegalReviewAction) -> dict:
    return {
        "id": action.id, "reviewer_id": action.reviewer_id,
        "target_type": action.target_type, "target_id": action.target_id,
        "action": action.action, "note": action.note, "from_status": action.from_status,
        "to_status": action.to_status, "created_at": action.created_at,
    }


@dataclass(frozen=True)
class ConsultationResult:
    row: LegalConsultation
    disclaimer: dict


class LegalWorkspaceModule:
    """Deep module for creating legal-workspace artefacts.

    The interface deliberately accepts plain values instead of HTTP/Pydantic
    objects.  Callers and tests can therefore cross this seam without knowing
    about FastAPI, while the implementation keeps persistence and governance
    rules in one place.
    """

    def __init__(self, *, audit: AuditLogService | None = None):
        self.audit = audit or AuditLogService()

    def _resolve_case_id(self, db: Session, user: User, case_id: int | None) -> int | None:
        """校验案件访问权限后返回 case_id；未关联返回 None，无权访问抛错。

        复用 verify_case_access：组织成员 + 严格案件成员 + 撤销状态一并校验，
        防止非成员向严格案件注入内容。
        """
        if case_id is None:
            return None
        try:
            verify_case_access(case_id, user.id, db)
        except HTTPException:
            raise LookupError("LEGAL_CASE_NOT_FOUND")
        return case_id

    async def create_consultation(
        self, db: Session, user: User, question: str, *, case_id: int | None = None,
    ) -> ConsultationResult:
        subscription_service.ensure_default_plans(db)
        if not subscription_service.check_quota(db, user.id, "consultation"):
            raise ValueError("QUOTA_EXCEEDED")

        case_id = self._resolve_case_id(db, user, case_id)
        ensure_demo_sources(db, user.id)
        sources = db.query(LegalSource).filter(
            LegalSource.user_id == user.id, LegalSource.status == "active"
        ).all()
        # E-7：LLM 调用前结束事务归还 DB 连接（LLM 等待 2-5s 期间不占用连接池）。
        # expunge 使已加载的 sources 脱离 session，避免 commit 后属性访问触发 N+1 重查。
        db.expunge_all()
        db.commit()
        category, known, missing, refs, advice, risk, status = await consultation_payload(
            question, sources, user_id=user.id, db=db
        )
        disclaimer = compute_disclaimer_level(risk_level=risk, category=category)
        row = LegalConsultation(
            user_id=user.id,
            case_id=case_id,
            question=question,
            category=category,
            known_facts_json=json.dumps(known, ensure_ascii=False),
            missing_facts_json=json.dumps(missing, ensure_ascii=False),
            references_json=json.dumps(refs, ensure_ascii=False),
            advice=advice,
            risk_level=risk,
            status=status,
            disclaimer_level=disclaimer["level"],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        subscription_service.record_usage(db, user.id, "consultation")
        self.audit.log(
            db, user, "legal_consultation_create", target_type="consultation",
            target_id=row.id, detail=f"category={category}, risk={risk}, case_id={case_id}",
        )
        return ConsultationResult(row=row, disclaimer=disclaimer)

    async def create_contract_review(
        self, db: Session, user: User, *, title: str, content: str,
        document_id: int | None = None, review_policy_id: int | None = None,
        review_policy_override: dict | None = None, case_id: int | None = None,
    ) -> ContractReview:
        subscription_service.ensure_default_plans(db)
        if not subscription_service.check_quota(db, user.id, "review"):
            raise ValueError("QUOTA_EXCEEDED")

        case_id = self._resolve_case_id(db, user, case_id)
        ensure_demo_sources(db, user.id)
        policy_snapshot = None
        policy_version = None
        if review_policy_id:
            policy = db.query(LegalReviewPolicy).filter(
                LegalReviewPolicy.id == review_policy_id,
                LegalReviewPolicy.organization_id == user.organization_id,
                LegalReviewPolicy.is_active == 1,
            ).first()
            if not policy:
                raise LookupError("LEGAL_REVIEW_POLICY_NOT_FOUND")
            version = db.query(LegalReviewPolicyVersion).filter(
                LegalReviewPolicyVersion.policy_id == policy.id,
                LegalReviewPolicyVersion.version == policy.version,
            ).first()
            policy_snapshot = json.loads(version.config_snapshot) if version else {
                "name": policy.name,
                "party_role": policy.party_role,
                "contract_type": policy.contract_type,
                "risk_preference": policy.risk_preference,
                "required_clauses": _json_or(policy.required_clauses_json, "[]"),
                "focus_points": policy.focus_points,
            }
            policy_version = policy.version
        if review_policy_override:
            policy_snapshot = {**(policy_snapshot or {}), **review_policy_override}

        review_input = content
        if policy_snapshot:
            review_input = f"审查策略（仅本次任务）：{json.dumps(policy_snapshot, ensure_ascii=False)}\n\n合同正文：\n{content}"
        # E-7：LLM 调用前归还 DB 连接，避免长等待期间占用连接池。
        db.commit()
        risks, summary = await review_contract(review_input, user_id=user.id)
        sources = db.query(LegalSource).filter(
            LegalSource.user_id == user.id, LegalSource.status == "active"
        ).all()
        refs = enrich_references(db, [ref_dict(s) for s in sources[:3]])
        row = ContractReview(
            user_id=user.id, document_id=document_id, title=title, content=content,
            case_id=case_id, summary=summary, risks_json=json.dumps(risks, ensure_ascii=False),
            references_json=json.dumps(refs, ensure_ascii=False),
            review_policy_id=review_policy_id, review_policy_version=policy_version,
            review_policy_snapshot_json=json.dumps(policy_snapshot or {}, ensure_ascii=False),
            status="needs_lawyer_review" if any(isinstance(item, dict) and item.get("risk_level") == "high" for item in risks) else "pending_review",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        subscription_service.record_usage(db, user.id, "review")
        self.audit.log(
            db, user, "legal_contract_review_create", target_type="contract_review",
            target_id=row.id, detail=f"risks={len(risks)}",
        )
        return row

    async def create_consultation_followup(
        self, db: Session, user: User, *, consultation_id: int, question: str,
    ) -> LegalConsultation:
        # 追问同样消耗咨询配额（与 create_consultation 一致），防止配额耗尽后无限追问
        subscription_service.ensure_default_plans(db)
        if not subscription_service.check_quota(db, user.id, "consultation"):
            raise ValueError("QUOTA_EXCEEDED")
        previous = db.query(LegalConsultation).filter(
            LegalConsultation.id == consultation_id, LegalConsultation.user_id == user.id
        ).first()
        if not previous:
            raise LookupError("LEGAL_CONSULTATION_NOT_FOUND")
        sources = db.query(LegalSource).filter(
            LegalSource.user_id == user.id, LegalSource.status == "active"
        ).all()
        # E-7：LLM 调用前归还 DB 连接；expunge 避免 commit 后重查。
        db.expunge_all()
        db.commit()
        category, known, missing, refs, advice, risk, status = await consultation_followup(
            previous.question, previous.advice or "", question, sources, user_id=user.id, db=db,
        )
        row = LegalConsultation(
            user_id=user.id, question=f"{previous.question}\n\n[追问] {question}",
            case_id=previous.case_id, category=category, known_facts_json=json.dumps(known, ensure_ascii=False),
            missing_facts_json=json.dumps(missing, ensure_ascii=False),
            references_json=json.dumps(refs, ensure_ascii=False), advice=advice,
            risk_level=risk, status=status,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        subscription_service.record_usage(db, user.id, "consultation")
        self.audit.log(
            db, user, "legal_followup_create", target_type="consultation",
            target_id=row.id, detail=f"followup to #{consultation_id}",
        )
        return row

    async def resubmit_contract_review(
        self, db: Session, user: User, *, review_id: int, title: str, content: str,
    ) -> ContractReview:
        row = db.query(ContractReview).filter(
            ContractReview.id == review_id, ContractReview.user_id == user.id
        ).first()
        if not row:
            raise LookupError("LEGAL_CONTRACT_REVIEW_NOT_FOUND")
        if row.status != "returned_for_facts":
            raise ValueError("LEGAL_CONTRACT_REVIEW_RESUBMIT_INVALID_STATUS")
        db.add(LegalDocumentVersion(
            target_type="contract_review", target_id=row.id, version=row.version,
            title=row.title, content=row.content, status_at_snapshot=row.status,
            snapshot_reason="resubmit", created_by=user.id,
        ))
        # E-7：先落版本快照并归还连接，再进入长 LLM 调用。
        db.commit()
        risks, summary = await review_contract(content, user_id=user.id)
        sources = db.query(LegalSource).filter(
            LegalSource.user_id == user.id, LegalSource.status == "active"
        ).all()
        refs = enrich_references(db, [ref_dict(s) for s in sources[:3]])
        row.title = title
        row.content = content
        row.version += 1
        row.summary = summary
        row.risks_json = json.dumps(risks, ensure_ascii=False)
        row.references_json = json.dumps(refs, ensure_ascii=False)
        row.status = "needs_lawyer_review" if any(isinstance(item, dict) and item.get("risk_level") == "high" for item in risks) else "pending_review"
        row.reviewer_id = None
        row.review_note = None
        row.reviewed_at = None
        db.commit()
        db.refresh(row)
        self.audit.log(
            db, user, "legal_contract_review_resubmit", target_type="contract_review",
            target_id=row.id, detail=f"version={row.version}",
        )
        return row

    async def create_draft(self, db: Session, user: User, *, document_type: str, fields: dict[str, str], case_id: int | None = None) -> tuple[LegalDraft, list[str]]:
        subscription_service.ensure_default_plans(db)
        if not subscription_service.check_quota(db, user.id, "draft"):
            raise ValueError("QUOTA_EXCEEDED")
        if document_type not in DRAFT_FIELDS:
            raise KeyError("LEGAL_DRAFT_TYPE_INVALID")
        case_id = self._resolve_case_id(db, user, case_id)
        required = DRAFT_REQUIRED_FIELDS.get(document_type, [])
        missing_required = [field for field in required if not fields.get(field)]
        missing = [field for field in DRAFT_FIELDS[document_type] if not fields.get(field)]
        sources = db.query(LegalSource).filter(
            LegalSource.user_id == user.id, LegalSource.status == "active"
        ).all()
        refs = enrich_references(db, [ref_dict(s) for s in sources[:3]])
        # E-7：LLM 调用前归还 DB 连接。
        db.commit()
        content = await draft_content(document_type, fields, missing, user_id=user.id)
        row = LegalDraft(
            user_id=user.id, document_type=document_type,
            case_id=case_id, title=DRAFT_TITLES[document_type],
            fields_json=json.dumps(fields, ensure_ascii=False),
            missing_fields_json=json.dumps(missing, ensure_ascii=False),
            references_json=json.dumps(refs, ensure_ascii=False), content=content,
            status="needs_facts" if missing_required else "pending_review",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        subscription_service.record_usage(db, user.id, "draft")
        self.audit.log(
            db, user, "legal_draft_create", target_type="draft", target_id=row.id,
            detail=f"type={document_type}, missing={len(missing)}",
        )
        return row, missing_required

    async def resubmit_draft(
        self, db: Session, user: User, *, draft_id: int, document_type: str, fields: dict[str, str],
    ) -> tuple[LegalDraft, list[str]]:
        row = db.query(LegalDraft).filter(LegalDraft.id == draft_id, LegalDraft.user_id == user.id).first()
        if not row:
            raise LookupError("LEGAL_DRAFT_NOT_FOUND")
        if row.status not in {"needs_facts", "returned_for_facts"}:
            raise ValueError("LEGAL_DRAFT_RESUBMIT_INVALID_STATUS")
        if document_type not in DRAFT_FIELDS:
            raise KeyError("LEGAL_DRAFT_TYPE_INVALID")
        db.add(LegalDocumentVersion(
            target_type="draft", target_id=row.id, version=row.version, title=row.title,
            content=row.content, status_at_snapshot=row.status, snapshot_reason="resubmit", created_by=user.id,
        ))
        # E-7：先落版本快照并归还连接，再进入长 LLM 调用。
        db.commit()
        required = DRAFT_REQUIRED_FIELDS.get(document_type, [])
        missing_required = [field for field in required if not fields.get(field)]
        missing = [field for field in DRAFT_FIELDS[document_type] if not fields.get(field)]
        sources = db.query(LegalSource).filter(
            LegalSource.user_id == user.id, LegalSource.status == "active"
        ).all()
        refs = enrich_references(db, [ref_dict(s) for s in sources[:3]])
        content = await draft_content(document_type, fields, missing, user_id=user.id)
        row.fields_json = json.dumps(fields, ensure_ascii=False)
        row.missing_fields_json = json.dumps(missing, ensure_ascii=False)
        row.references_json = json.dumps(refs, ensure_ascii=False)
        row.content = content
        row.version += 1
        row.status = "needs_facts" if missing_required else "pending_review"
        row.reviewer_id = None
        row.review_note = None
        row.reviewed_at = None
        db.commit()
        db.refresh(row)
        self.audit.log(
            db, user, "legal_draft_resubmit", target_type="draft", target_id=row.id,
            detail=f"version={row.version}",
        )
        return row, missing_required


class LegalWorkspaceReadModule:
    """Read and review module for legal-workspace artefacts.

    HTTP handlers use this interface for all workspace queries and review
    transitions, so serialization, authorization and audit semantics do not
    drift between consultation, contract and draft endpoints.
    """

    def __init__(self, *, audit: AuditLogService | None = None):
        self.audit = audit or AuditLogService()

    def overview(self, db: Session, user: User) -> dict:
        ensure_demo_sources(db, user.id)
        return {
            "brand": "律智检",
            "organization_id": user.organization_id,
            "disclaimer": DISCLAIMER,
            "workflows": [
                {"key": "consultation", "label": "法律咨询", "description": "分类事实、定位法源、形成一般性处理建议"},
                {"key": "contract_review", "label": "合同审查", "description": "按条款类型定位风险并保留原文证据"},
                {"key": "draft", "label": "文书草稿", "description": "支持四类文书模板，缺失事实明确待补充"},
                {"key": "review", "label": "律师审核", "description": "审批、退回补充、线下处理和归档"},
            ],
            "counts": {
                "sources": db.query(LegalSource).filter(LegalSource.user_id == user.id).count(),
                "consultations": db.query(LegalConsultation).filter(LegalConsultation.user_id == user.id).count(),
                "contract_reviews": db.query(ContractReview).filter(ContractReview.user_id == user.id).count(),
                "drafts": db.query(LegalDraft).filter(LegalDraft.user_id == user.id).count(),
            },
        }

    def metrics(self, db: Session, user: User) -> dict:
        consultations = db.query(LegalConsultation).filter(LegalConsultation.user_id == user.id).all()
        reviews = db.query(ContractReview).filter(ContractReview.user_id == user.id).all()
        drafts = db.query(LegalDraft).filter(LegalDraft.user_id == user.id).all()
        status_counts: dict[str, int] = {}
        for row in consultations + reviews + drafts:
            status_counts[row.status] = status_counts.get(row.status, 0) + 1
        total_consultations = len(consultations)
        reference_coverage = round(
            sum(bool(row.references_json and row.references_json.strip() not in ("", "[]")) for row in consultations)
            / total_consultations * 100, 1
        ) if total_consultations else 0
        return {
            "totals": {"consultations": total_consultations, "contract_reviews": len(reviews), "drafts": len(drafts)},
            "reference_coverage_pct": reference_coverage,
            "draft_adoption_pct": round(sum(row.status == "lawyer_approved" for row in drafts) / len(drafts) * 100, 1) if drafts else 0,
            "high_risk_consultations": sum(row.risk_level == "high" for row in consultations),
            "high_risk_reviews": sum(row.status == "needs_lawyer_review" for row in reviews),
            "returned_for_facts": sum(row.status == "returned_for_facts" for row in reviews),
            "status_distribution": status_counts,
            "approved_drafts": sum(row.status == "lawyer_approved" for row in drafts),
        }

    def list_rows(self, db: Session, user: User, kind: str) -> list[dict]:
        model = {"consultation": LegalConsultation, "contract_review": ContractReview, "draft": LegalDraft}[kind]
        rows = db.query(model).filter(model.user_id == user.id).order_by(model.created_at.desc()).limit(50).all()
        return [serialize_workspace_row(row) for row in rows]

    def get_row(self, db: Session, user: User, kind: str, item_id: int):
        model = {"consultation": LegalConsultation, "contract_review": ContractReview, "draft": LegalDraft}[kind]
        row = db.query(model).filter(model.id == item_id, model.user_id == user.id).first()
        if not row:
            raise LookupError(f"LEGAL_{kind.upper()}_NOT_FOUND")
        return row

    def versions(self, db: Session, user: User, kind: str, item_id: int) -> list[dict]:
        self.get_row(db, user, kind, item_id)
        return [serialize_workspace_version(version) for version in db.query(LegalDocumentVersion).filter(
            LegalDocumentVersion.target_type == kind,
            LegalDocumentVersion.target_id == item_id,
        ).order_by(LegalDocumentVersion.version.desc()).all()]

    def templates(self) -> list[dict]:
        return [{"key": key, "label": label} for key, label in WORKSPACE_TEMPLATE_LABELS.items()]

    def review_queue(self, db: Session, user: User) -> list[dict]:
        reviewer = user.role in {"admin", "dept_admin"}
        items: list[dict] = []
        for model, kind in ((LegalConsultation, "consultation"), (ContractReview, "contract_review"), (LegalDraft, "draft")):
            query = db.query(model).filter(model.status.in_(["pending_review", "needs_lawyer_review", "needs_facts"]))
            if not reviewer:
                query = query.filter(model.user_id == user.id)
            for row in query.order_by(model.created_at.desc()).limit(50).all():
                item = serialize_workspace_row(row)
                item["target_type"] = kind
                items.append(item)
        return items

    def apply_review_action(self, db: Session, user: User, *, target_type: str, target_id: int, action: str, note: str | None):
        row = target_query(db, target_type, target_id)
        if not row:
            raise LookupError("LEGAL_REVIEW_TARGET_NOT_FOUND")
        reviewer = user.role in {"admin", "dept_admin"}
        if action in REVIEWER_ACTIONS:
            if not reviewer:
                raise PermissionError("LEGAL_REVIEW_FORBIDDEN")
            status = REVIEWER_ACTIONS[action]
        elif action in OWNER_ACTIONS:
            if not (reviewer or row.user_id == user.id):
                raise PermissionError("LEGAL_REVIEW_FORBIDDEN")
            status = OWNER_ACTIONS[action]
        else:
            raise ValueError("LEGAL_REVIEW_ACTION_INVALID")
        previous = row.status
        row.status = status
        if action in REVIEWER_ACTIONS:
            row.reviewer_id, row.review_note, row.reviewed_at = user.id, note, utc_now()
        db.add(LegalReviewAction(reviewer_id=user.id, target_type=target_type, target_id=target_id, action=action, note=note, from_status=previous, to_status=status))
        db.commit()
        db.refresh(row)
        self.audit.log(db, user, f"legal_review_{action}", target_type=target_type, target_id=target_id, detail=f"{previous}->{status}")
        return serialize_workspace_row(row)

    def add_review_comment(self, db: Session, user: User, *, target_type: str, target_id: int, note: str) -> dict:
        row = target_query(db, target_type, target_id)
        if not row:
            raise LookupError("LEGAL_REVIEW_TARGET_NOT_FOUND")
        if not (row.user_id == user.id or user.role in {"admin", "dept_admin"}):
            raise PermissionError("LEGAL_REVIEW_COMMENT_FORBIDDEN")
        comment = LegalReviewAction(reviewer_id=user.id, target_type=target_type, target_id=target_id, action="comment", note=note, from_status=row.status, to_status=row.status)
        db.add(comment)
        db.commit()
        db.refresh(comment)
        self.audit.log(db, user, "legal_review_comment", target_type=target_type, target_id=target_id, detail=note[:100])
        return serialize_review_action(comment)

    def review_history(self, db: Session, user: User, *, target_type: str, target_id: int) -> dict:
        row = target_query(db, target_type, target_id)
        if not row:
            raise LookupError("LEGAL_REVIEW_TARGET_NOT_FOUND")
        if user.role not in {"admin", "dept_admin"} and row.user_id != user.id:
            raise PermissionError("LEGAL_REVIEW_HISTORY_FORBIDDEN")
        result = serialize_workspace_row(row)
        result["target_type"] = target_type
        result["history"] = [serialize_review_action(item) for item in db.query(LegalReviewAction).filter(
            LegalReviewAction.target_type == target_type, LegalReviewAction.target_id == target_id,
        ).order_by(LegalReviewAction.created_at.desc()).all()]
        return result

    def review_stats(self, db: Session, user: User) -> dict:
        if user.role not in {"admin", "dept_admin"}:
            raise PermissionError("LEGAL_REVIEW_STATS_FORBIDDEN")
        actions = db.query(LegalReviewAction).order_by(LegalReviewAction.created_at.desc()).all()
        action_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        reasons: list[dict] = []
        for item in actions:
            action_counts[item.action] = action_counts.get(item.action, 0) + 1
            type_counts[item.target_type] = type_counts.get(item.target_type, 0) + 1
            if item.action == "return" and item.note:
                reasons.append({"target_type": item.target_type, "target_id": item.target_id, "note": item.note, "created_at": item.created_at})
        return {"total_actions": len(actions), "action_distribution": action_counts, "target_type_distribution": type_counts, "return_reasons": reasons[:50], "recent_actions": [serialize_review_action(item) for item in actions[:20]]}


legal_workspace_module = LegalWorkspaceModule()
legal_workspace_read_module = LegalWorkspaceReadModule()
