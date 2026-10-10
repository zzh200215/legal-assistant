"""Application module for the legal-workspace write workflows.

The HTTP layer should only translate requests and responses.  This module owns
the orchestration that is shared by the legal workspace endpoints: quota
checks, source selection, LLM execution, persistence, and audit records.
"""

import json
import difflib
from dataclasses import dataclass

from sqlalchemy.orm import Session

from fastapi import HTTPException

from app.core.auth import verify_case_access
from app.core.time import utc_now
from app.models.legal import (
    ContractReview,
    LegalCase,
    LegalConsultation,
    LegalDocumentVersion,
    LegalDocumentComment,
    LegalDraft,
    LegalReviewAction,
    LegalSource,
)
from app.models.legal_contract import LegalReviewPolicy, LegalReviewPolicyVersion
from app.models.user import User
from app.models.org import LegalMemberRole, OrganizationMember
from app.services.observability.audit_log_service import AuditLogService
from app.services.legal.legal_service import (
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
from app.services.legal.legal_reference_service import enrich_references
from app.services.legal.legal_domain_service import legal_domain_service
from app.services.billing.subscription_service import subscription_service
from app.services.legal.matter_service import matter_service


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

# P1 状态机：每个审核动作的合法来源状态，非法转换由服务端拒绝。
# return 允许从 lawyer_approved 触发：已审内容可被退回修订，使 approved -> superseded（重新进入新版本审核闭环）。
REVIEW_ACTION_FROM = {
    "approve": {"pending_review", "needs_lawyer_review", "needs_facts"},
    "return": {"pending_review", "needs_lawyer_review", "needs_facts", "lawyer_approved"},
    "offline": {"pending_review", "needs_lawyer_review", "needs_facts", "returned_for_facts"},
    "close": {"pending_review", "needs_lawyer_review", "needs_facts", "returned_for_facts", "offline_consultation"},
    "submit_review": {"draft", "pending_review", "needs_lawyer_review", "returned_for_facts", "needs_facts"},
}


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
            "reviewed_at": row.reviewed_at, "review_due_at": getattr(row, "review_due_at", None),
            "created_at": row.created_at,
            "confidence": compute_confidence(row),
            "feedback_score": row.feedback_score,
            "reviewed_version": getattr(row, "reviewed_version", None),
            "model_snapshot": _json_or(getattr(row, "model_snapshot_json", None), "{}"),
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
            "reviewed_at": row.reviewed_at, "review_due_at": getattr(row, "review_due_at", None),
            "created_at": row.created_at,
            "confidence": compute_confidence(row),
            "feedback_score": row.feedback_score,
            "reviewed_version": getattr(row, "reviewed_version", None),
            "is_final": bool(getattr(row, "is_final", 0)),
            "model_snapshot": _json_or(getattr(row, "model_snapshot_json", None), "{}"),
        }
    return {
        "id": row.id, "case_id": row.case_id, "document_type": row.document_type, "title": row.title,
        "fields": _json_or(row.fields_json, "{}"),
        "missing_fields": _json_or(row.missing_fields_json, "[]"),
        "references": _json_or(row.references_json, "[]"), "content": row.content,
        "version": row.version, "status": row.status, "reviewer_id": row.reviewer_id,
        "review_note": row.review_note, "reviewed_at": row.reviewed_at,
        "review_due_at": getattr(row, "review_due_at", None),
        "created_at": row.created_at,
        "confidence": compute_confidence(row),
        "feedback_score": row.feedback_score,
        "reviewed_version": getattr(row, "reviewed_version", None),
        "row_version": getattr(row, "row_version", None),
        "updated_at": getattr(row, "updated_at", None),
        "is_final": bool(getattr(row, "is_final", 0)),
        "model_snapshot": _json_or(getattr(row, "model_snapshot_json", None), "{}"),
    }


def _draft_missing_fields(document_type: str, fields: dict[str, str]) -> list[str]:
    return [field for field in DRAFT_FIELDS.get(document_type, []) if not fields.get(field)]


def serialize_workspace_version(version: LegalDocumentVersion) -> dict:
    return {
        "id": version.id, "target_type": version.target_type, "target_id": version.target_id,
        "version": version.version, "title": version.title, "content": version.content,
        "fields": _json_or(getattr(version, "fields_json", None), "{}"),
        "version_note": getattr(version, "version_note", None),
        "status_at_snapshot": version.status_at_snapshot,
        "snapshot_reason": version.snapshot_reason, "created_by": version.created_by,
        "created_at": version.created_at,
    }


def serialize_document_comment(comment: LegalDocumentComment, author: User | None = None) -> dict:
    return {
        "id": comment.id,
        "target_type": comment.target_type,
        "target_id": comment.target_id,
        "version": comment.version,
        "line_start": comment.line_start,
        "line_end": comment.line_end,
        "body": comment.body,
        "mentions": _json_or(comment.mentions_json, "[]"),
        "status": comment.status,
        "author_id": comment.author_id,
        "author_name": (author.full_name or author.username) if author else None,
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
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

    def _notify_generation_done(
        self, db: Session, user: User, *, title: str, body: str,
        reference_type: str, reference_id: int, case_id: int | None,
    ) -> None:
        """LLM 生成完成（咨询/审查/文书）写一条站内通知，点击可直达结果（ux-audit M-9）。

        同步生成约 20-30s，用户可能切走；通知让用户回来后能一键定位结果。
        通知失败只记日志，绝不影响生成主流程。
        """
        try:
            from app.services.notification.notification_service import CHANNEL_SITE, notification_service

            if not user.organization_id:
                return
            notification_service.create_notification(
                db=db,
                organization_id=user.organization_id,
                user_id=user.id,
                event_type="workflow",
                title=title,
                body=body,
                channel=CHANNEL_SITE,
                case_id=case_id,
                reference_type=reference_type,
                reference_id=reference_id,
            )
        except Exception:  # noqa: BLE001 - notification failure cannot affect generation
            import logging

            logging.getLogger(__name__).warning(
                "generation notification failed for %s=%s", reference_type, reference_id, exc_info=True,
            )

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

    def _capture_flow_snapshot(
        self, db: Session, user: User, *, case_id: int | None = None, document_id: int | None = None,
    ) -> str | None:
        """长流程启动时创建权限快照（合同审查 / 文书生成）。"""
        from app.services.org.authorization_service import authorization_service

        try:
            ctx = authorization_service.build_context(db, user, org_id=user.organization_id)
            return authorization_service.capture_snapshot(
                db, user, ctx,
                case_ids=[case_id] if case_id else [],
                document_ids=[document_id] if document_id else [],
            )
        except Exception:
            return None

    def _assert_flow_snapshot(
        self, db: Session, user: User, snapshot_id: str | None, *, case_id: int | None = None,
    ) -> None:
        """LLM 生成后、落库前校验快照：硬撤销（禁用/退出/成员/授权/严格案件成员撤销）立即终止。"""
        if not snapshot_id:
            return
        from app.services.org.authorization_service import authorization_service

        authorization_service.assert_snapshot(db, snapshot_id, user_id=user.id)
        if case_id is not None:
            try:
                verify_case_access(case_id, user.id, db)
            except HTTPException:
                raise LookupError("LEGAL_CASE_NOT_FOUND")

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
        legal_domain_service.persist_consultation_artifacts(
            db, row, known=known, missing=missing, refs=refs, risk_level=risk,
        )
        subscription_service.record_usage(db, user.id, "consultation")
        matter_service.record_activity(
            db, case_id=row.case_id, organization_id=user.organization_id, actor_id=user.id,
            event_type="consultation.created", target_type="consultation", target_id=row.id,
            title="新增法律咨询", summary=row.question[:300],
        )
        self.audit.log(
            db, user, "legal_consultation_create", target_type="consultation",
            target_id=row.id, detail=f"category={category}, risk={risk}, case_id={case_id}",
        )
        self._notify_generation_done(
            db, user, title="法律咨询已完成",
            body=f"「{row.question[:40]}」的分析结果已生成，点击查看建议。",
            reference_type="consultation", reference_id=row.id, case_id=row.case_id,
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
        # 长流程权限快照：审查期间权限范围保持稳定，硬撤销立即终止落库。
        flow_snapshot = self._capture_flow_snapshot(db, user, case_id=case_id, document_id=document_id)
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
        # 硬撤销（禁用/强制退出/成员/授权/严格案件成员撤销）→ 立即终止，不落库。
        self._assert_flow_snapshot(db, user, flow_snapshot, case_id=case_id)
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
        legal_domain_service.persist_review_artifacts(db, row, risks=risks, refs=refs)
        subscription_service.record_usage(db, user.id, "review")
        matter_service.record_activity(
            db, case_id=row.case_id, organization_id=user.organization_id, actor_id=user.id,
            event_type="contract_review.created", target_type="contract_review", target_id=row.id,
            title="提交合同审查", summary=row.title,
        )
        self.audit.log(
            db, user, "legal_contract_review_create", target_type="contract_review",
            target_id=row.id, detail=f"risks={len(risks)}",
        )
        self._notify_generation_done(
            db, user, title="合同审查已完成",
            body=f"「{row.title or '未命名合同'}」的风险分析已生成（{len(risks)} 项风险），点击查看。",
            reference_type="contract_review", reference_id=row.id, case_id=row.case_id,
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
        # 追问前重验案件访问权限：用户若已从严格案件被撤销成员，不得继续追加内容。
        if previous.case_id:
            try:
                verify_case_access(previous.case_id, user.id, db)
            except Exception:
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
        legal_domain_service.persist_consultation_artifacts(
            db, row, known=known, missing=missing, refs=refs, risk_level=risk,
        )
        subscription_service.record_usage(db, user.id, "consultation")
        matter_service.record_activity(
            db, case_id=row.case_id, organization_id=user.organization_id, actor_id=user.id,
            event_type="consultation.followup_created", target_type="consultation", target_id=row.id,
            title="追加法律咨询", summary=question[:300],
        )
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
        row.review_due_at = None
        row.review_note = None
        row.reviewed_at = None
        row.reviewed_version = None
        row.is_final = 0
        db.commit()
        db.refresh(row)
        # P1：进入新版本后旧版本未决风险项/主张标记为被取代，再持久化新版本结构化工件。
        legal_domain_service.supersede_artifacts(db, user, "contract_review", row.id)
        legal_domain_service.persist_review_artifacts(db, row, risks=risks, refs=refs)
        matter_service.record_activity(
            db, case_id=row.case_id, organization_id=user.organization_id, actor_id=user.id,
            event_type="contract_review.resubmitted", target_type="contract_review", target_id=row.id,
            title="重新提交合同审查", summary=f"版本 {row.version}",
        )
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
        # 长流程权限快照：文书生成期间权限范围保持稳定。
        flow_snapshot = self._capture_flow_snapshot(db, user, case_id=case_id)
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
        # 硬撤销（禁用/强制退出/成员/严格案件成员撤销）→ 立即终止，不落库。
        self._assert_flow_snapshot(db, user, flow_snapshot, case_id=case_id)
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
        legal_domain_service.persist_draft_artifacts(db, row, missing_fields=missing, refs=refs)
        subscription_service.record_usage(db, user.id, "draft")
        matter_service.record_activity(
            db, case_id=row.case_id, organization_id=user.organization_id, actor_id=user.id,
            event_type="draft.created", target_type="draft", target_id=row.id,
            title="生成法律文书", summary=row.title,
        )
        self.audit.log(
            db, user, "legal_draft_create", target_type="draft",
            target_id=row.id, detail=f"type={document_type}, missing={len(missing)}",
        )
        self._notify_generation_done(
            db, user, title="文书草稿已生成",
            body=f"「{row.title}」已生成，点击查看与编辑。",
            reference_type="draft", reference_id=row.id, case_id=row.case_id,
        )
        return row, missing_required

    async def resubmit_draft(
        self, db: Session, user: User, *, draft_id: int, document_type: str, fields: dict[str, str], content_override: str | None = None,
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
            content=row.content, fields_json=row.fields_json,
            status_at_snapshot=row.status, snapshot_reason="resubmit", created_by=user.id,
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
        content = content_override if content_override is not None else await draft_content(document_type, fields, missing, user_id=user.id)
        row.fields_json = json.dumps(fields, ensure_ascii=False)
        row.missing_fields_json = json.dumps(missing, ensure_ascii=False)
        row.references_json = json.dumps(refs, ensure_ascii=False)
        row.content = content
        row.version += 1
        row.status = "needs_facts" if missing_required else "pending_review"
        row.reviewer_id = None
        row.review_due_at = None
        row.review_note = None
        row.reviewed_at = None
        row.reviewed_version = None
        row.is_final = 0
        db.commit()
        db.refresh(row)
        legal_domain_service.supersede_artifacts(db, user, "draft", row.id)
        legal_domain_service.persist_draft_artifacts(db, row, missing_fields=missing, refs=refs)
        matter_service.record_activity(
            db, case_id=row.case_id, organization_id=user.organization_id, actor_id=user.id,
            event_type="draft.resubmitted", target_type="draft", target_id=row.id,
            title="重新提交文书", summary=f"版本 {row.version}",
        )
        self.audit.log(
            db, user, "legal_draft_resubmit", target_type="draft", target_id=row.id,
            detail=f"version={row.version}",
        )
        return row, missing_required

    def _load_editable_draft(self, db: Session, user: User, draft_id: int, base_row_version: int | None = None) -> LegalDraft:
        row = db.query(LegalDraft).filter(LegalDraft.id == draft_id, LegalDraft.user_id == user.id).first()
        if not row:
            raise LookupError("LEGAL_DRAFT_NOT_FOUND")
        if base_row_version is not None and int(row.row_version or 0) != int(base_row_version):
            raise ValueError("LEGAL_DRAFT_EDIT_CONFLICT")
        if row.status in {"lawyer_approved", "archived", "offline_consultation"} or getattr(row, "is_final", 0):
            raise ValueError("LEGAL_DRAFT_EDIT_LOCKED")
        return row

    def autosave_draft(
        self, db: Session, user: User, *, draft_id: int, document_type: str,
        fields: dict[str, str], content: str, base_row_version: int | None = None,
    ) -> LegalDraft:
        row = self._load_editable_draft(db, user, draft_id, base_row_version)
        if document_type not in DRAFT_FIELDS:
            raise KeyError("LEGAL_DRAFT_TYPE_INVALID")
        normalized_fields = {str(key): str(value or "") for key, value in (fields or {}).items()}
        row.fields_json = json.dumps(normalized_fields, ensure_ascii=False)
        row.missing_fields_json = json.dumps(_draft_missing_fields(document_type, normalized_fields), ensure_ascii=False)
        row.content = content or ""
        row.document_type = document_type
        row.title = DRAFT_TITLES.get(document_type, row.title)
        db.commit()
        db.refresh(row)
        self.audit.log(db, user, "legal_draft_autosave", target_type="draft", target_id=row.id, detail=f"version={row.version}")
        return row

    def save_draft_version(
        self, db: Session, user: User, *, draft_id: int, document_type: str,
        fields: dict[str, str], content: str, base_row_version: int | None = None,
        version_note: str | None = None,
    ) -> LegalDraft:
        row = self._load_editable_draft(db, user, draft_id, base_row_version)
        if document_type not in DRAFT_FIELDS:
            raise KeyError("LEGAL_DRAFT_TYPE_INVALID")
        normalized_fields = {str(key): str(value or "") for key, value in (fields or {}).items()}
        next_version = int(row.version or 1) + 1
        row.version = next_version
        row.document_type = document_type
        row.title = DRAFT_TITLES.get(document_type, row.title)
        row.fields_json = json.dumps(normalized_fields, ensure_ascii=False)
        row.missing_fields_json = json.dumps(_draft_missing_fields(document_type, normalized_fields), ensure_ascii=False)
        row.content = content or ""
        db.add(LegalDocumentVersion(
            target_type="draft", target_id=row.id, version=next_version, title=row.title,
            content=row.content, fields_json=json.dumps(normalized_fields, ensure_ascii=False),
            version_note=(version_note or "").strip()[:512] or None,
            status_at_snapshot=row.status, snapshot_reason="manual", created_by=user.id,
        ))
        db.commit()
        db.refresh(row)
        matter_service.record_activity(
            db, case_id=row.case_id, organization_id=user.organization_id, actor_id=user.id,
            event_type="draft.version_saved", target_type="draft", target_id=row.id,
            title="保存文书版本", summary=f"版本 {row.version}",
        )
        self.audit.log(db, user, "legal_draft_version_save", target_type="draft", target_id=row.id, detail=f"version={row.version}")
        return row

    def _load_collaborative_draft(self, db: Session, user: User, draft_id: int) -> LegalDraft:
        row = db.query(LegalDraft).filter(LegalDraft.id == draft_id).first()
        if not row:
            raise LookupError("LEGAL_DRAFT_NOT_FOUND")
        allowed = row.user_id == user.id or row.reviewer_id == user.id or user.role in {"admin", "dept_admin"}
        if not allowed:
            raise PermissionError("LEGAL_DRAFT_COLLABORATION_FORBIDDEN")
        return row

    def list_draft_comments(self, db: Session, user: User, *, draft_id: int, status: str | None = None) -> list[dict]:
        row = self._load_collaborative_draft(db, user, draft_id)
        query = db.query(LegalDocumentComment).filter(
            LegalDocumentComment.target_type == "draft", LegalDocumentComment.target_id == row.id,
        )
        if status in {"open", "resolved"}:
            query = query.filter(LegalDocumentComment.status == status)
        comments = query.order_by(LegalDocumentComment.created_at.asc(), LegalDocumentComment.id.asc()).all()
        author_ids = {comment.author_id for comment in comments}
        authors = {item.id: item for item in db.query(User).filter(User.id.in_(author_ids)).all()} if author_ids else {}
        return [serialize_document_comment(comment, authors.get(comment.author_id)) for comment in comments]

    def add_draft_comment(
        self, db: Session, user: User, *, draft_id: int, body: str, version: int | None = None,
        line_start: int | None = None, line_end: int | None = None, mentions: list[int] | None = None,
    ) -> dict:
        row = self._load_collaborative_draft(db, user, draft_id)
        if not body or not body.strip():
            raise ValueError("LEGAL_DRAFT_COMMENT_EMPTY")
        if line_start is not None and line_start < 1:
            raise ValueError("LEGAL_DRAFT_COMMENT_LINE_INVALID")
        if line_end is not None and line_end < (line_start or 1):
            raise ValueError("LEGAL_DRAFT_COMMENT_LINE_INVALID")
        if version is not None and version < 1:
            raise ValueError("LEGAL_DRAFT_COMMENT_VERSION_INVALID")
        comment = LegalDocumentComment(
            target_type="draft", target_id=row.id, version=version or row.version,
            line_start=line_start, line_end=line_end, body=body.strip()[:4000],
            mentions_json=json.dumps([int(item) for item in (mentions or [])[:20]], ensure_ascii=False),
            author_id=user.id, status="open",
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)
        self.audit.log(db, user, "legal_draft_comment_add", target_type="draft", target_id=row.id, detail=f"version={comment.version}")
        return serialize_document_comment(comment, user)

    def resolve_draft_comment(self, db: Session, user: User, *, draft_id: int, comment_id: int, status: str) -> dict:
        row = self._load_collaborative_draft(db, user, draft_id)
        if status not in {"open", "resolved"}:
            raise ValueError("LEGAL_DRAFT_COMMENT_STATUS_INVALID")
        comment = db.query(LegalDocumentComment).filter(
            LegalDocumentComment.id == comment_id,
            LegalDocumentComment.target_type == "draft",
            LegalDocumentComment.target_id == row.id,
        ).first()
        if not comment:
            raise LookupError("LEGAL_DRAFT_COMMENT_NOT_FOUND")
        comment.status = status
        db.commit()
        db.refresh(comment)
        self.audit.log(db, user, "legal_draft_comment_status", target_type="draft", target_id=row.id, detail=f"comment={comment.id};status={status}")
        author = db.query(User).filter(User.id == comment.author_id).first()
        return serialize_document_comment(comment, author)

    def draft_collaboration(self, db: Session, user: User, *, draft_id: int) -> dict:
        row = self._load_collaborative_draft(db, user, draft_id)
        people = {row.user_id, row.reviewer_id} - {None}
        comments = db.query(LegalDocumentComment.author_id).filter(
            LegalDocumentComment.target_type == "draft", LegalDocumentComment.target_id == row.id,
        ).distinct().all()
        people.update(item[0] for item in comments)
        users = {item.id: item for item in db.query(User).filter(User.id.in_(people)).all()} if people else {}
        collaborators = [
            {"id": person_id, "name": users[person_id].full_name or users[person_id].username,
             "role": "负责人" if person_id == row.user_id else "审核人" if person_id == row.reviewer_id else "批注人"}
            for person_id in people if person_id in users
        ]
        collaborators.sort(key=lambda item: (item["role"], item["name"]))
        return {
            "draft_id": row.id,
            "row_version": row.row_version,
            "document_version": row.version,
            "updated_at": row.updated_at,
            "status": row.status,
            "locked": bool(getattr(row, "is_final", 0) or row.status in {"lawyer_approved", "archived", "offline_consultation"}),
            "collaborators": collaborators,
        }

    def diff_draft_versions(self, db: Session, user: User, *, draft_id: int, from_id: int, to_id: int) -> dict:
        row = self._load_collaborative_draft(db, user, draft_id)
        versions = db.query(LegalDocumentVersion).filter(
            LegalDocumentVersion.target_type == "draft", LegalDocumentVersion.target_id == row.id,
            LegalDocumentVersion.id.in_([from_id, to_id]),
        ).all()
        by_id = {item.id: item for item in versions}
        if from_id not in by_id or to_id not in by_id:
            raise LookupError("LEGAL_DRAFT_VERSION_NOT_FOUND")
        before, after = by_id[from_id], by_id[to_id]
        lines = []
        for item in difflib.ndiff((before.content or "").splitlines(), (after.content or "").splitlines()):
            prefix, text = item[0], item[2:]
            if prefix == " ":
                kind = "same"
            elif prefix == "+":
                kind = "added"
            elif prefix == "-":
                kind = "removed"
            else:
                continue
            lines.append({"kind": kind, "text": text})
        return {"from": serialize_workspace_version(before), "to": serialize_workspace_version(after), "rows": lines}

    def restore_draft_version(
        self, db: Session, user: User, *, draft_id: int, version_id: int,
        base_row_version: int | None = None,
    ) -> LegalDraft:
        row = self._load_editable_draft(db, user, draft_id, base_row_version)
        snapshot = db.query(LegalDocumentVersion).filter(
            LegalDocumentVersion.id == version_id,
            LegalDocumentVersion.target_type == "draft",
            LegalDocumentVersion.target_id == draft_id,
        ).first()
        if not snapshot:
            raise LookupError("LEGAL_DRAFT_VERSION_NOT_FOUND")
        current_version = int(row.version or 1)
        db.add(LegalDocumentVersion(
            target_type="draft", target_id=row.id, version=current_version, title=row.title,
            content=row.content, fields_json=row.fields_json,
            status_at_snapshot=row.status, snapshot_reason="restore", created_by=user.id,
        ))
        row.content = snapshot.content or ""
        if snapshot.fields_json:
            row.fields_json = snapshot.fields_json
            try:
                restored_fields = json.loads(snapshot.fields_json)
            except (TypeError, json.JSONDecodeError):
                restored_fields = {}
            row.missing_fields_json = json.dumps(
                _draft_missing_fields(row.document_type, restored_fields), ensure_ascii=False,
            )
        row.title = snapshot.title or row.title
        row.version = current_version + 1
        row.status = "draft"
        row.reviewer_id = None
        row.review_due_at = None
        row.review_note = None
        row.reviewed_at = None
        row.reviewed_version = None
        row.is_final = 0
        db.commit()
        db.refresh(row)
        matter_service.record_activity(
            db, case_id=row.case_id, organization_id=user.organization_id, actor_id=user.id,
            event_type="draft.version_restored", target_type="draft", target_id=row.id,
            title="恢复文书版本", summary=f"从版本 {snapshot.version} 恢复为版本 {row.version}",
        )
        self.audit.log(db, user, "legal_draft_version_restore", target_type="draft", target_id=row.id, detail=f"source_version={snapshot.version};version={row.version}")
        return row


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

    def list_rows(self, db: Session, user: User, kind: str, case_id: int | None = None) -> list[dict]:
        model = {"consultation": LegalConsultation, "contract_review": ContractReview, "draft": LegalDraft}[kind]
        query = db.query(model).filter(model.user_id == user.id)
        if case_id is not None:
            verify_case_access(case_id, user.id, db)
            query = query.filter(model.case_id == case_id)
        rows = query.order_by(model.created_at.desc()).limit(50).all()
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

    def _is_legal_reviewer(self, db: Session, user: User) -> bool:
        if user.role in {"admin", "dept_admin"}:
            return True
        if not getattr(user, "organization_id", None):
            return False
        member = db.query(OrganizationMember).filter(
            OrganizationMember.organization_id == user.organization_id,
            OrganizationMember.user_id == user.id,
        ).first()
        return bool(member and member.legal_role in {LegalMemberRole.admin.value, LegalMemberRole.reviewer.value})

    def review_queue(
        self, db: Session, user: User, case_id: int | None = None, assigned_to: int | None = None,
        status: str | None = None, overdue: bool | None = None, search: str | None = None,
    ) -> list[dict]:
        reviewer = self._is_legal_reviewer(db, user)
        if user.organization_id:
            member = db.query(OrganizationMember).filter(
                OrganizationMember.organization_id == user.organization_id,
                OrganizationMember.user_id == user.id,
            ).first()
            reviewer = reviewer or bool(member and member.legal_role in {LegalMemberRole.admin.value, LegalMemberRole.reviewer.value})
        if case_id is not None:
            verify_case_access(case_id, user.id, db)
        items: list[dict] = []
        case_cache: dict[int, LegalCase | None] = {}
        for model, kind in ((LegalConsultation, "consultation"), (ContractReview, "contract_review"), (LegalDraft, "draft")):
            query = db.query(model)
            if status:
                statuses = {item.strip() for item in status.split(",") if item.strip()}
                query = query.filter(model.status.in_(statuses))
            else:
                query = query.filter(model.status.in_(["pending_review", "needs_lawyer_review", "needs_facts"]))
            if case_id is not None:
                query = query.filter(model.case_id == case_id)
            if assigned_to is not None:
                query = query.filter(model.reviewer_id == assigned_to)
            if not reviewer:
                query = query.filter(model.user_id == user.id)
            elif user.role == "dept_admin":
                # 部门管理员仅可见本组织提交的待审项，防止跨租户读取审核队列。
                if user.organization_id is None:
                    continue
                org_user_ids = db.query(User.id).filter(
                    User.organization_id == user.organization_id,
                )
                query = query.filter(model.user_id.in_(org_user_ids))
            elif reviewer and user.role not in {"admin", "dept_admin"}:
                org_user_ids = db.query(User.id).filter(User.organization_id == user.organization_id)
                query = query.filter(model.user_id.in_(org_user_ids))
            for row in query.order_by(model.created_at.desc()).limit(50).all():
                item = serialize_workspace_row(row)
                item["target_type"] = kind
                if row.case_id is not None:
                    if row.case_id not in case_cache:
                        case_cache[row.case_id] = db.query(LegalCase).filter(LegalCase.id == row.case_id).first()
                    matter = case_cache[row.case_id]
                    if matter:
                        item["case_title"] = matter.title
                        item["case_status"] = matter.status
                due_at = getattr(row, "review_due_at", None)
                item["review_overdue"] = bool(due_at and due_at < utc_now() and row.status in ("pending_review", "needs_lawyer_review", "needs_facts"))
                if overdue is True and not item["review_overdue"]:
                    continue
                if overdue is False and item["review_overdue"]:
                    continue
                if search:
                    needle = search.strip().lower()
                    haystack = " ".join(str(item.get(key) or "") for key in ("title", "question", "case_title", "summary", "content")).lower()
                    if needle not in haystack:
                        continue
                items.append(item)
        return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)

    def bulk_assign_reviewers(self, db: Session, user: User, *, items: list[dict], reviewer_id: int | None, due_at=None) -> list[dict]:
        if not self._is_legal_reviewer(db, user):
            raise PermissionError("LEGAL_REVIEW_ASSIGN_FORBIDDEN")
        return [self.assign_reviewer(
            db, user, target_type=item.get("target_type"), target_id=int(item.get("target_id")),
            reviewer_id=reviewer_id, due_at=due_at,
        ) for item in items]

    def bulk_review_action(self, db: Session, user: User, *, items: list[dict], action: str, note: str | None = None) -> list[dict]:
        if not self._is_legal_reviewer(db, user):
            raise PermissionError("LEGAL_REVIEW_FORBIDDEN")
        return [self.apply_review_action(
            db, user, target_type=item.get("target_type"), target_id=int(item.get("target_id")),
            action=action, note=note,
        ) for item in items]

    def review_sla(self, db: Session, user: User) -> dict:
        if not self._is_legal_reviewer(db, user):
            raise PermissionError("LEGAL_REVIEW_STATS_FORBIDDEN")
        queue = self.review_queue(db, user)
        active = [item for item in queue if item.get("status") in {"pending_review", "needs_lawyer_review", "needs_facts"}]
        overdue_count = sum(1 for item in active if item.get("review_overdue"))
        assigned_count = sum(1 for item in active if item.get("reviewer_id"))
        due_count = sum(1 for item in active if item.get("review_due_at"))
        completed = []
        for model in (LegalConsultation, ContractReview, LegalDraft):
            query = db.query(model).filter(model.reviewed_at.isnot(None))
            if user.role not in {"admin", "dept_admin"}:
                query = query.filter(model.reviewer_id == user.id)
            for row in query.limit(500).all():
                if row.created_at and row.reviewed_at:
                    completed.append(max(0.0, (row.reviewed_at - row.created_at).total_seconds() / 3600))
        average_hours = round(sum(completed) / len(completed), 1) if completed else None
        return {
            "active_count": len(active), "overdue_count": overdue_count,
            "assigned_count": assigned_count, "unassigned_count": len(active) - assigned_count,
            "due_count": due_count, "average_turnaround_hours": average_hours,
            "overdue_rate": round(overdue_count / len(active), 4) if active else 0,
        }

    def assign_reviewer(self, db: Session, user: User, *, target_type: str, target_id: int,
                        reviewer_id: int | None, due_at=None) -> dict:
        row = target_query(db, target_type, target_id)
        if not row:
            raise LookupError("LEGAL_REVIEW_TARGET_NOT_FOUND")
        member = db.query(OrganizationMember).filter(
            OrganizationMember.organization_id == user.organization_id,
            OrganizationMember.user_id == user.id,
        ).first()
        if not member or (user.role not in {"admin", "dept_admin"} and member.legal_role not in {LegalMemberRole.admin.value, LegalMemberRole.reviewer.value}):
            raise PermissionError("LEGAL_REVIEW_ASSIGN_FORBIDDEN")
        if getattr(row, "case_id", None):
            verify_case_access(row.case_id, user.id, db)
        if reviewer_id is not None:
            target_member = db.query(OrganizationMember).filter(
                OrganizationMember.organization_id == user.organization_id,
                OrganizationMember.user_id == reviewer_id,
                OrganizationMember.legal_role.in_([LegalMemberRole.admin.value, LegalMemberRole.reviewer.value]),
            ).first()
            if not target_member:
                raise ValueError("LEGAL_REVIEW_ASSIGNEE_INVALID")
        row.reviewer_id = reviewer_id
        row.review_due_at = due_at
        db.commit()
        db.refresh(row)
        matter_service.record_activity(
            db, case_id=getattr(row, "case_id", None), organization_id=user.organization_id, actor_id=user.id,
            event_type="review.assigned", target_type=target_type, target_id=target_id,
            title="审核任务已分配", summary=f"reviewer_id={reviewer_id or 'unassigned'}",
        )
        return serialize_workspace_row(row)

    def apply_review_action(self, db: Session, user: User, *, target_type: str, target_id: int, action: str, note: str | None):
        row = target_query(db, target_type, target_id)
        if not row:
            raise LookupError("LEGAL_REVIEW_TARGET_NOT_FOUND")
        reviewer = self._is_legal_reviewer(db, user)
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
        # P1 状态机守卫：非法来源状态转换由服务端拒绝。
        valid_from = REVIEW_ACTION_FROM.get(action)
        if valid_from and previous not in valid_from:
            raise ValueError(f"LEGAL_REVIEW_ILLEGAL_TRANSITION:{previous}->{status}")
        row.status = status
        if action in REVIEWER_ACTIONS:
            row.reviewer_id, row.review_note, row.reviewed_at = user.id, note, utc_now()
        if action == "approve" and hasattr(row, "version"):
            # P1 审核绑定版本：通过时冻结已审内容到版本快照，供"修改需重审"校验。
            row.reviewed_version = row.version
            db.add(LegalDocumentVersion(
                target_type=target_type, target_id=target_id, version=row.version,
                title=getattr(row, "title", None), content=getattr(row, "content", "") or "",
                fields_json=getattr(row, "fields_json", None) if isinstance(row, LegalDraft) else None,
                status_at_snapshot="lawyer_approved", snapshot_reason="manual", created_by=user.id,
            ))
        if action == "return" and previous == "lawyer_approved":
            # P1 approved -> superseded：已审内容被退回修订，旧审批失效，须重审后才能再发布。
            row.reviewed_version = None
            if hasattr(row, "is_final"):
                row.is_final = 0
        db.add(LegalReviewAction(
            reviewer_id=user.id, target_type=target_type, target_id=target_id, action=action,
            note=note, from_status=previous, to_status=status,
            target_version=getattr(row, "version", None),
        ))
        db.commit()
        db.refresh(row)
        matter_service.record_activity(
            db, case_id=getattr(row, "case_id", None), organization_id=getattr(user, "organization_id", None), actor_id=user.id,
            event_type=f"review.{action}", target_type=target_type, target_id=target_id,
            title=f"审核动作：{action}", summary=f"{previous} -> {status}",
        )
        self.audit.log(db, user, f"legal_review_{action}", target_type=target_type, target_id=target_id, detail=f"{previous}->{status}")
        return serialize_workspace_row(row)

    def add_review_comment(self, db: Session, user: User, *, target_type: str, target_id: int, note: str) -> dict:
        row = target_query(db, target_type, target_id)
        if not row:
            raise LookupError("LEGAL_REVIEW_TARGET_NOT_FOUND")
        if not (row.user_id == user.id or self._is_legal_reviewer(db, user)):
            raise PermissionError("LEGAL_REVIEW_COMMENT_FORBIDDEN")
        comment = LegalReviewAction(reviewer_id=user.id, target_type=target_type, target_id=target_id, action="comment", note=note, from_status=row.status, to_status=row.status, target_version=getattr(row, "version", None))
        db.add(comment)
        db.commit()
        db.refresh(comment)
        matter_service.record_activity(
            db, case_id=getattr(row, "case_id", None), organization_id=getattr(user, "organization_id", None), actor_id=user.id,
            event_type="review.comment_added", target_type=target_type, target_id=target_id,
            title="新增审核批注", summary=note[:300],
        )
        self.audit.log(db, user, "legal_review_comment", target_type=target_type, target_id=target_id, detail=note[:100])
        return serialize_review_action(comment)

    def review_history(self, db: Session, user: User, *, target_type: str, target_id: int) -> dict:
        row = target_query(db, target_type, target_id)
        if not row:
            raise LookupError("LEGAL_REVIEW_TARGET_NOT_FOUND")
        if not self._is_legal_reviewer(db, user) and row.user_id != user.id:
            raise PermissionError("LEGAL_REVIEW_HISTORY_FORBIDDEN")
        result = serialize_workspace_row(row)
        result["target_type"] = target_type
        result["history"] = [serialize_review_action(item) for item in db.query(LegalReviewAction).filter(
            LegalReviewAction.target_type == target_type, LegalReviewAction.target_id == target_id,
        ).order_by(LegalReviewAction.created_at.desc()).all()]
        return result

    def review_stats(self, db: Session, user: User) -> dict:
        if not self._is_legal_reviewer(db, user):
            raise PermissionError("LEGAL_REVIEW_STATS_FORBIDDEN")
        query = db.query(LegalReviewAction)
        if user.role == "dept_admin" or user.role not in {"admin", "dept_admin"}:
            # 审核动作表无组织维度，部门管理员仅统计本人操作，防止跨租户读取。
            query = query.filter(LegalReviewAction.reviewer_id == user.id)
        actions = query.order_by(LegalReviewAction.created_at.desc()).all()
        action_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        reasons: list[dict] = []
        for item in actions:
            action_counts[item.action] = action_counts.get(item.action, 0) + 1
            type_counts[item.target_type] = type_counts.get(item.target_type, 0) + 1
            if item.action == "return" and item.note:
                reasons.append({"target_type": item.target_type, "target_id": item.target_id, "note": item.note, "created_at": item.created_at})
        return {
            "total_actions": len(actions), "action_distribution": action_counts,
            "target_type_distribution": type_counts, "return_reasons": reasons[:50],
            "recent_actions": [serialize_review_action(item) for item in actions[:20]],
            "sla": self.review_sla(db, user),
        }


legal_workspace_module = LegalWorkspaceModule()
legal_workspace_read_module = LegalWorkspaceReadModule()
