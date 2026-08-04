from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.api_response import api_error, should_passthrough_exception
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.email import EmailDraftOut
from app.schemas.workflow import MeetingTaskConfirmRequest, RiskFollowupDraftRequest, WeeklyReportDraftRequest
from app.services.email_service import email_service
from app.services.workflow_service import workflow_service

router = APIRouter()


@router.get("/meetings/{meeting_id}/task-preview")
def preview_meeting_tasks(meeting_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return workflow_service.preview_meeting_tasks(meeting_id, db=db, user=current_user)
    except ValueError as exc:
        message = str(exc)
        raise api_error(404, "会议或会议纪要不存在", code="WORKFLOW_MEETING_NOT_FOUND", detail=message)


@router.post("/meetings/{meeting_id}/confirm-tasks")
def confirm_meeting_tasks(
    meeting_id: int,
    req: MeetingTaskConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return workflow_service.confirm_meeting_tasks(meeting_id, req.items, db=db, user=current_user)
    except ValueError as exc:
        raise api_error(400, "会议任务确认失败", code="WORKFLOW_TASK_CONFIRM_INVALID", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "会议任务确认失败", code="WORKFLOW_TASK_CONFIRM_FAILED", detail=str(exc))


@router.post("/weekly-report", response_model=EmailDraftOut)
def create_weekly_report(
    req: WeeklyReportDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        draft = workflow_service.create_weekly_report_draft(
            db=db,
            user=current_user,
            scope=req.scope,
            start_date=req.start_date,
            end_date=req.end_date,
            recipient=req.recipient,
            title=req.title,
        )
        return EmailDraftOut.model_validate(email_service.serialize_draft(draft))
    except ValueError as exc:
        raise api_error(400, "周报草稿生成失败", code="WORKFLOW_WEEKLY_REPORT_INVALID", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "周报草稿生成失败", code="WORKFLOW_WEEKLY_REPORT_FAILED", detail=str(exc))


@router.post("/meetings/{meeting_id}/risk-followup", response_model=EmailDraftOut)
def create_risk_followup(
    meeting_id: int,
    req: RiskFollowupDraftRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        draft = workflow_service.create_risk_followup_draft(meeting_id, db=db, user=current_user, recipient=req.recipient)
        return EmailDraftOut.model_validate(email_service.serialize_draft(draft))
    except ValueError as exc:
        raise api_error(400, "风险待办草稿生成失败", code="WORKFLOW_RISK_FOLLOWUP_INVALID", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "风险待办草稿生成失败", code="WORKFLOW_RISK_FOLLOWUP_FAILED", detail=str(exc))
