from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.api_response import api_error, paginated_payload, should_passthrough_exception
from app.core.database import get_db
from app.models.email import EmailDraft
from app.models.user import User
from app.schemas.email import (
    EmailDraftActionResponse,
    EmailDraftOut,
    EmailGenerateRequest,
    EmailPolishRequest,
    EmailReplyRequest,
    EmailSwitchToneRequest,
    EmailThreadReplyRequest,
    TaskSyncEmailRequest,
    EmailThreadSummaryRequest,
)
from app.services.email_service import email_service
from app.services.task_service import task_service

router = APIRouter()


@router.post("/generate", response_model=EmailDraftActionResponse)
async def generate_email(req: EmailGenerateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        result = await email_service.generate(
            purpose=req.purpose,
            key_points=req.key_points,
            tone=req.tone,
            recipient=req.recipient,
            need_action=req.need_action,
            user_id=current_user.id,
            db=db,
        )
        return EmailDraftActionResponse(
            draft=EmailDraftOut.model_validate(email_service.serialize_draft(result["draft"])),
            subject_candidates=result["subject_candidates"],
        )
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "邮件生成失败", code="EMAIL_GENERATE_FAILED", detail=str(e))


@router.post("/reply", response_model=EmailDraftActionResponse)
async def reply_email(req: EmailReplyRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        result = await email_service.reply(
            original_email=req.original_email,
            reply_goal=req.reply_goal,
            tone=req.tone,
            recipient=req.recipient,
            user_id=current_user.id,
            db=db,
        )
        return EmailDraftActionResponse(
            draft=EmailDraftOut.model_validate(email_service.serialize_draft(result["draft"])),
            subject_candidates=result["subject_candidates"],
        )
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "邮件回复生成失败", code="EMAIL_REPLY_FAILED", detail=str(e))


@router.post("/from-tasks", response_model=EmailDraftActionResponse)
async def generate_email_from_tasks(
    req: TaskSyncEmailRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        tasks = task_service.list_for_sync(
            current_user.id,
            db,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
            scope=req.scope,
            task_ids=req.task_ids,
            include_overdue_only=req.include_overdue_only,
        )
        if not tasks:
            raise api_error(404, "没有可同步的未完成任务", code="TASK_SYNC_SOURCE_EMPTY")

        result = await email_service.generate(
            purpose=f"{req.purpose}{'（仅逾期任务）' if req.include_overdue_only else ''}",
            key_points=task_service.build_sync_email_points(tasks),
            tone=req.tone,
            recipient=req.recipient,
            need_action=req.need_action,
            generation_type="task_sync",
            metadata={
                "source_type": "task_sync",
                "task_ids": [task.id for task in tasks],
                "task_scope": req.scope,
                "include_overdue_only": req.include_overdue_only,
            },
            user_id=current_user.id,
            db=db,
        )
        return EmailDraftActionResponse(
            draft=EmailDraftOut.model_validate(email_service.serialize_draft(result["draft"])),
            subject_candidates=result["subject_candidates"],
        )
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "任务同步邮件生成失败", code="EMAIL_FROM_TASKS_FAILED", detail=str(e))


@router.post("/{draft_id}/switch-tone", response_model=EmailDraftActionResponse)
async def switch_tone(draft_id: int, req: EmailSwitchToneRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        result = await email_service.switch_tone(draft_id, req.target_tone, db, user_id=current_user.id)
        return EmailDraftActionResponse(
            draft=EmailDraftOut.model_validate(email_service.serialize_draft(result["draft"])),
            subject_candidates=result["subject_candidates"],
        )
    except ValueError as e:
        raise api_error(404, "邮件草稿不存在", code="EMAIL_DRAFT_NOT_FOUND", detail=str(e))
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "邮件语气切换失败", code="EMAIL_SWITCH_TONE_FAILED", detail=str(e))


@router.post("/thread-summary")
async def summarize_thread(req: EmailThreadSummaryRequest, current_user: User = Depends(get_current_user)):
    try:
        return await email_service.summarize_thread(req.emails, user_id=current_user.id)
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "邮件线程总结失败", code="EMAIL_THREAD_SUMMARY_FAILED", detail=str(e))


@router.post("/thread-reply", response_model=EmailDraftActionResponse)
async def reply_from_thread(req: EmailThreadReplyRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        result = await email_service.reply_from_thread(
            emails=req.emails,
            reply_goal=req.reply_goal,
            tone=req.tone,
            recipient=req.recipient,
            user_id=current_user.id,
            db=db,
        )
        return EmailDraftActionResponse(
            draft=EmailDraftOut.model_validate(email_service.serialize_draft(result["draft"])),
            subject_candidates=result["subject_candidates"],
        )
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "线程回复生成失败", code="EMAIL_THREAD_REPLY_FAILED", detail=str(e))


@router.get("/{draft_id}", response_model=EmailDraftOut)
def get_draft(draft_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    draft = email_service.get(
        draft_id,
        db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    if not draft:
        raise api_error(404, "邮件草稿不存在", code="EMAIL_DRAFT_NOT_FOUND")
    return EmailDraftOut.model_validate(email_service.serialize_draft(draft))


@router.post("/{draft_id}/polish", response_model=EmailDraftOut)
async def polish_draft(draft_id: int, req: EmailPolishRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        draft = await email_service.polish(draft_id, req.instruction, db=db, user_id=current_user.id)
        return EmailDraftOut.model_validate(email_service.serialize_draft(draft))
    except ValueError as e:
        raise api_error(404, "邮件草稿不存在", code="EMAIL_DRAFT_NOT_FOUND", detail=str(e))
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "邮件润色失败", code="EMAIL_POLISH_FAILED", detail=str(e))


@router.get("/")
def list_drafts(
    scope: str = Query("all", description="可见范围筛选：all/mine/department/organization/shared"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    visible_rows = email_service.list_visible(
        db=db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
        scope=scope,
    )
    total = len(visible_rows)
    drafts = visible_rows[(page - 1) * page_size : page * page_size]
    items = [EmailDraftOut.model_validate(email_service.serialize_draft(draft)).model_dump() for draft in drafts]
    return paginated_payload(items, total=total, page=page, page_size=page_size)
