"""Matter facade for authorization, overview, artifacts and activity history."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.auth import verify_case_access
from app.models.document import Document
from app.models.legal import ContractReview, LegalCase, LegalConsultation, LegalDraft, LegalReviewAction
from app.models.legal_portal import LegalDeadline
from app.models.matter_activity import MatterActivity
from app.models.workflow import WorkflowRun


class MatterService:
    """Expose a small, permission-checked interface for a case and its work."""

    @staticmethod
    def require_case(db: Session, *, case_id: int, user_id: int) -> LegalCase:
        access = verify_case_access(case_id, user_id, db)
        return access["case"]

    def summary(self, db: Session, *, case_id: int, user_id: int) -> dict:
        case = self.require_case(db, case_id=case_id, user_id=user_id)
        consultations = db.query(LegalConsultation).filter(LegalConsultation.case_id == case_id).count()
        reviews = db.query(ContractReview).filter(ContractReview.case_id == case_id).count()
        drafts = db.query(LegalDraft).filter(LegalDraft.case_id == case_id).count()
        documents = db.query(Document).filter(
            Document.organization_id == case.organization_id,
            Document.metadata_json.like(f'%"case_id"%{case_id}%'),
        ).count()
        pending_reviews = sum((
            db.query(model).filter(model.case_id == case_id, model.status.in_(
                ("pending_review", "needs_lawyer_review", "needs_facts")
            )).count()
            for model in (LegalConsultation, ContractReview, LegalDraft)
        ))
        pending_tasks = db.query(WorkflowRun).filter(
            WorkflowRun.case_id == case_id,
            WorkflowRun.status.in_(("queued", "running", "retrying", "waiting_input", "waiting_approval")),
        ).count()
        upcoming_deadlines = db.query(LegalDeadline).filter(
            LegalDeadline.case_id == case_id,
            LegalDeadline.status.in_(("active", "due")),
        ).count()
        return {
            "case": {
                "id": case.id, "organization_id": case.organization_id, "title": case.title,
                "case_type": case.case_type, "status": case.status,
                "client_name": case.client_name, "opposing_party": case.opposing_party,
                "description": case.description, "user_id": case.user_id,
                "created_at": case.created_at, "updated_at": case.updated_at,
            },
            "counts": {
                "consultations": consultations, "contract_reviews": reviews,
                "drafts": drafts, "documents": documents, "pending_reviews": pending_reviews,
                "pending_tasks": pending_tasks, "upcoming_deadlines": upcoming_deadlines,
            },
        }

    def artifacts(self, db: Session, *, case_id: int, user_id: int, limit: int = 100) -> list[dict]:
        self.require_case(db, case_id=case_id, user_id=user_id)
        items: list[dict] = []
        for model, kind, title_fn, status_fn in (
            (LegalConsultation, "consultation", lambda row: row.question[:160], lambda row: row.status),
            (ContractReview, "contract_review", lambda row: row.title, lambda row: row.status),
            (LegalDraft, "draft", lambda row: row.title, lambda row: row.status),
        ):
            rows = db.query(model).filter(model.case_id == case_id).all()
            items.extend({
                "type": kind, "id": row.id, "title": title_fn(row),
                "status": status_fn(row), "created_at": row.created_at,
                "updated_at": row.updated_at,
            } for row in rows)
        items.sort(key=lambda item: item.get("updated_at") or item.get("created_at"), reverse=True)
        return items[:max(1, min(limit, 200))]

    def activity(self, db: Session, *, case_id: int, user_id: int, limit: int = 100) -> list[dict]:
        self.require_case(db, case_id=case_id, user_id=user_id)
        case = self.require_case(db, case_id=case_id, user_id=user_id)
        rows = db.query(MatterActivity).filter(
            MatterActivity.case_id == case_id,
            MatterActivity.organization_id == case.organization_id,
        ).order_by(MatterActivity.created_at.desc(), MatterActivity.id.desc()).limit(
            max(1, min(limit, 200))
        ).all()
        output = [{
            "id": row.id, "event_type": row.event_type, "target_type": row.target_type,
            "target_id": row.target_id, "title": row.title, "summary": row.summary,
            "actor_id": row.actor_id, "visibility": row.visibility, "created_at": row.created_at,
        } for row in rows]

        # Backfill a useful initial timeline for matters created before activity tracking.
        if len(output) < limit:
            legacy = db.query(LegalReviewAction).filter(
                LegalReviewAction.target_type.in_(("consultation", "contract_review", "draft")),
            ).order_by(LegalReviewAction.created_at.desc()).limit(200).all()
            artifact_keys = {(item["type"], item["id"]) for item in self.artifacts(
                db, case_id=case_id, user_id=user_id, limit=200,
            )}
            seen = {(item["target_type"], item["target_id"], item["event_type"]) for item in output}
            for action in legacy:
                if (action.target_type, action.target_id) not in artifact_keys:
                    continue
                key = (action.target_type, action.target_id, action.action)
                if key in seen:
                    continue
                output.append({
                    "id": f"legacy-{action.id}", "event_type": action.action,
                    "target_type": action.target_type, "target_id": action.target_id,
                    "title": f"审核状态：{action.to_status}", "summary": action.note,
                    "actor_id": action.reviewer_id, "visibility": "internal",
                    "created_at": action.created_at,
                })
                seen.add(key)
        output.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
        return output[:max(1, min(limit, 200))]

    @staticmethod
    def record_activity(
        db: Session, *, case_id: int | None, organization_id: int | None,
        actor_id: int | None, event_type: str, title: str,
        target_type: str | None = None, target_id: int | None = None,
        summary: str | None = None, visibility: str = "internal",
    ) -> MatterActivity | None:
        if not case_id or not organization_id:
            return None
        row = MatterActivity(
            case_id=case_id, organization_id=organization_id, actor_id=actor_id,
            event_type=event_type, target_type=target_type, target_id=target_id,
            title=title[:256], summary=summary[:1000] if summary else None,
            visibility=visibility,
        )
        db.add(row)
        return row


matter_service = MatterService()
