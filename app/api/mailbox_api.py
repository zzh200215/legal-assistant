from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.api_response import api_error, paginated_payload, should_passthrough_exception
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.connector import ConnectorOut
from app.schemas.mailbox import ImapMailboxCreateRequest, MailboxAutoReplyRequest, MailboxMessageOut, MailboxRetentionRequest, MailboxTaskConfirmRequest, MailboxTaskSuggestionOut
from app.services.email_service import email_service
from app.services.outbound_email_service import outbound_email_service
from app.schemas.task import TaskOut
from app.services.connector_service import connector_service
from app.services.mailbox_service import mailbox_service
from app.services.task_service import task_service

router = APIRouter()


@router.post("/retention/preview")
def preview_retention(req: MailboxRetentionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return mailbox_service.retention_preview(
        db=db, user=current_user, retention_days=req.retention_days, connector_id=req.connector_id,
    )


@router.post("/retention/purge")
def purge_retention(req: MailboxRetentionRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return mailbox_service.purge_retained_messages(
        db=db, user=current_user, retention_days=req.retention_days, connector_id=req.connector_id,
    )


@router.post("/connectors", response_model=ConnectorOut)
def create_imap_connector(
    req: ImapMailboxCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        connector = mailbox_service.create_imap_connector(db=db, user=current_user, request=req)
        return ConnectorOut(**connector_service.serialize_connector(connector))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(400, "邮箱连接创建失败", code="IMAP_CONNECTOR_CREATE_FAILED", detail=str(exc))


@router.get("/emails")
def list_mailbox_messages(
    connector_id: int | None = Query(None),
    category: str | None = Query(None),
    importance: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows, total = mailbox_service.list_messages(
        db=db,
        user=current_user,
        connector_id=connector_id,
        category=category,
        importance=importance,
        page=page,
        page_size=page_size,
    )
    items = [MailboxMessageOut.model_validate({**row.__dict__, "priority_score": mailbox_service.priority_score(row)}).model_dump() for row in rows]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/emails/top-important")
def top_important_emails(limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = mailbox_service.top_important(db=db, user=current_user, limit=limit)
    return [MailboxMessageOut.model_validate({**row.__dict__, "priority_score": mailbox_service.priority_score(row)}).model_dump() for row in rows]


@router.get("/reply-style-profile")
def reply_style_profile(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return mailbox_service.reply_style_profile(db=db, user=current_user)


@router.post("/emails/{message_id}/auto-reply")
async def auto_reply(message_id: int, req: MailboxAutoReplyRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    message = mailbox_service.get_message(message_id, db=db, user=current_user)
    if not message:
        raise api_error(404, "邮件不存在", code="MAILBOX_MESSAGE_NOT_FOUND")
    profile = mailbox_service.reply_style_profile(db=db, user=current_user)
    recipient = req.recipient or message.sender
    result = await email_service.reply(
        original_email=f"发件人：{message.sender or ''}\n主题：{message.subject or ''}\n正文：{message.body_text or message.summary or ''}\n\n回复策略：{profile['instruction']}",
        reply_goal=req.reply_goal,
        tone=profile["tone"], recipient=recipient, user_id=current_user.id, db=db,
    )
    payload = {"draft": email_service.serialize_draft(result["draft"]), "reply_profile": profile, "send_request": None}
    if req.smtp_connector_id:
        try:
            request = outbound_email_service.request_send(result["draft"].id, connector_id=req.smtp_connector_id, db=db, user=current_user)
            payload["send_request"] = outbound_email_service.serialize_request(request, db=db, viewer=current_user)
        except ValueError as exc:
            raise api_error(400, "回复草稿已生成，但发送申请创建失败", code="AUTO_REPLY_SEND_REQUEST_INVALID", detail=str(exc))
    return payload


@router.get("/emails/{message_id}/task-suggestion", response_model=MailboxTaskSuggestionOut)
def get_task_suggestion(message_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return MailboxTaskSuggestionOut(**mailbox_service.task_suggestion(message_id, db=db, user=current_user))
    except ValueError as exc:
        raise api_error(404, "邮件不存在", code="MAILBOX_MESSAGE_NOT_FOUND", detail=str(exc))


@router.post("/emails/{message_id}/confirm-task", response_model=TaskOut)
def confirm_mailbox_task(
    message_id: int,
    req: MailboxTaskConfirmRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        task = mailbox_service.confirm_task(message_id, db=db, user=current_user, request=req)
        return TaskOut.model_validate(task_service.serialize_task(task))
    except ValueError as exc:
        raise api_error(404, "邮件任务确认失败", code="MAILBOX_TASK_CONFIRM_INVALID", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "邮件任务确认失败", code="MAILBOX_TASK_CONFIRM_FAILED", detail=str(exc))
