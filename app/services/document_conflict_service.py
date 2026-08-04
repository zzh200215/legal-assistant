from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.document import DocumentConflictCase
from app.core.time import utc_now
from app.models.user import User
from app.services.oplog_service import oplog_service
from app.services.task_service import task_service


class DocumentConflictService:
    VALID_STATUSES = {"pending_confirmation", "task_created", "in_progress", "resolved", "false_positive"}

    @staticmethod
    def _serialize(case: DocumentConflictCase) -> dict:
        try:
            document_ids = json.loads(case.document_ids_json)
        except json.JSONDecodeError:
            document_ids = []
        try:
            conflict = json.loads(case.conflict_json)
        except json.JSONDecodeError:
            conflict = {}
        return {
            "id": case.id,
            "document_ids": document_ids if isinstance(document_ids, list) else [],
            "conflict": conflict if isinstance(conflict, dict) else {},
            "status": case.status,
            "task_id": case.task_id,
            "resolution_note": case.resolution_note,
            "resolved_at": case.resolved_at,
            "created_at": case.created_at,
            "updated_at": case.updated_at,
        }

    @staticmethod
    def _assert_confirmable(conflict: dict) -> None:
        if not isinstance(conflict, dict) or not conflict.get("evidence_complete"):
            raise ValueError("Conflict evidence is incomplete")
        if not isinstance(conflict.get("source_a"), dict) or not isinstance(conflict.get("source_b"), dict):
            raise ValueError("Conflict sources are invalid")

    def create_suggestions(self, conflicts: list[dict], *, document_ids: list[int], db: Session, user: User) -> list[dict]:
        created = []
        for conflict in conflicts:
            if not isinstance(conflict, dict) or not conflict.get("evidence_complete"):
                continue
            self._assert_confirmable(conflict)
            case = DocumentConflictCase(
                user_id=user.id,
                organization_id=user.organization_id,
                department_id=user.department_id,
                document_ids_json=json.dumps(sorted({int(item) for item in document_ids}), ensure_ascii=False),
                conflict_json=json.dumps(conflict, ensure_ascii=False),
            )
            db.add(case)
            db.commit()
            db.refresh(case)
            created.append(self._serialize(case))
        if created:
            oplog_service.log(
                module="document_conflict",
                action="conflict_suggestions_created",
                db=db,
                user_id=user.id,
                target_type="document_conflict_case",
                target_id=created[0]["id"],
                detail=f"count={len(created)}; document_ids={document_ids}",
            )
        return created

    def list_cases(self, *, db: Session, user: User, status: str | None = None) -> list[dict]:
        query = db.query(DocumentConflictCase).filter(DocumentConflictCase.user_id == user.id)
        if status:
            query = query.filter(DocumentConflictCase.status == status)
        return [self._serialize(case) for case in query.order_by(DocumentConflictCase.created_at.desc(), DocumentConflictCase.id.desc()).all()]

    def confirm_task(self, case_id: int, *, db: Session, user: User, title: str | None, assignee: str | None, priority: str | None) -> dict:
        case = db.query(DocumentConflictCase).filter(DocumentConflictCase.id == case_id, DocumentConflictCase.user_id == user.id).first()
        if not case:
            raise ValueError("Conflict case not found")
        if case.status != "pending_confirmation":
            raise ValueError("Conflict case is not pending confirmation")
        payload = self._serialize(case)["conflict"]
        self._assert_confirmable(payload)
        source_a, source_b = payload["source_a"], payload["source_b"]
        field_label = payload.get("field_label") or "事实"
        task_title = (title or f"核对{field_label}冲突：{payload.get('field') or '待确认'}").strip()
        description = "\n".join(
            [
                f"冲突字段：{field_label}｜{payload.get('field') or '未命名字段'}",
                f"来源 A：{source_a.get('document_title')} = {source_a.get('value')}",
                f"原文 A：{source_a.get('source_text')}",
                f"定位 A：第 {source_a.get('page_number') or '-'} 页 {source_a.get('section_title') or ''}".strip(),
                f"来源 B：{source_b.get('document_title')} = {source_b.get('value')}",
                f"原文 B：{source_b.get('source_text')}",
                f"定位 B：第 {source_b.get('page_number') or '-'} 页 {source_b.get('section_title') or ''}".strip(),
                f"建议动作：{payload.get('recommended_action') or '确认最终业务口径。'}",
                f"冲突案例 ID：{case.id}",
            ]
        )
        task = task_service.create(
            title=task_title,
            user_id=user.id,
            db=db,
            description=description,
            assignee=(assignee or "").strip() or None,
            priority=priority if priority in {"low", "medium", "high"} else payload.get("severity") or "medium",
            source_type="document_conflict",
            source_id=case.id,
        )
        case.status = "task_created"
        case.task_id = task.id
        db.add(case)
        db.commit()
        db.refresh(case)
        oplog_service.log(module="document_conflict", action="conflict_task_confirmed", db=db, user_id=user.id, target_type="task", target_id=task.id, detail=f"case_id={case.id}")
        return {"case": self._serialize(case), "task": task_service.serialize_task(task)}

    def update_status(self, case_id: int, *, db: Session, user: User, status: str, resolution_note: str | None) -> dict:
        if status not in self.VALID_STATUSES - {"pending_confirmation", "task_created"}:
            raise ValueError("Invalid conflict status")
        case = db.query(DocumentConflictCase).filter(DocumentConflictCase.id == case_id, DocumentConflictCase.user_id == user.id).first()
        if not case:
            raise ValueError("Conflict case not found")
        case.status = status
        case.resolution_note = (resolution_note or "").strip() or None
        case.resolved_at = utc_now() if status in {"resolved", "false_positive"} else None
        db.add(case)
        db.commit()
        db.refresh(case)
        oplog_service.log(module="document_conflict", action="conflict_status_updated", db=db, user_id=user.id, target_type="document_conflict_case", target_id=case.id, detail=f"status={status}")
        return self._serialize(case)


document_conflict_service = DocumentConflictService()
