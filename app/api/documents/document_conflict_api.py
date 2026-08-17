from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.api_response import api_error, should_passthrough_exception
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.documents.document_conflict_service import document_conflict_service

router = APIRouter()


class ConflictSuggestionRequest(BaseModel):
    document_ids: list[int]
    conflicts: list[dict]


class ConflictTaskConfirmRequest(BaseModel):
    title: str | None = None
    assignee: str | None = None
    priority: str | None = None


class ConflictStatusRequest(BaseModel):
    status: str
    resolution_note: str | None = None


@router.post("/suggestions")
def create_conflict_suggestions(req: ConflictSuggestionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if len(req.document_ids) < 2:
        raise api_error(400, "至少需要两份文档", code="CONFLICT_DOCUMENTS_INVALID")
    try:
        return {"items": document_conflict_service.create_suggestions(req.conflicts, document_ids=req.document_ids, db=db, user=current_user)}
    except ValueError as exc:
        raise api_error(400, "冲突建议创建失败", code="CONFLICT_SUGGESTION_INVALID", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "冲突建议创建失败", code="CONFLICT_SUGGESTION_FAILED", detail=str(exc))


@router.get("/")
def list_conflict_cases(status: str | None = Query(None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return {"items": document_conflict_service.list_cases(db=db, user=current_user, status=status)}


@router.post("/{case_id}/confirm-task")
def confirm_conflict_task(case_id: int, req: ConflictTaskConfirmRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return document_conflict_service.confirm_task(case_id, db=db, user=current_user, title=req.title, assignee=req.assignee, priority=req.priority)
    except ValueError as exc:
        raise api_error(400, "冲突任务确认失败", code="CONFLICT_TASK_CONFIRM_INVALID", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "冲突任务确认失败", code="CONFLICT_TASK_CONFIRM_FAILED", detail=str(exc))


@router.patch("/{case_id}/status")
def update_conflict_status(case_id: int, req: ConflictStatusRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return document_conflict_service.update_status(case_id, db=db, user=current_user, status=req.status, resolution_note=req.resolution_note)
    except ValueError as exc:
        raise api_error(400, "冲突状态更新失败", code="CONFLICT_STATUS_INVALID", detail=str(exc))
