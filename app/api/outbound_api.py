from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.api_response import api_error, should_passthrough_exception
from app.core.auth import get_current_user, require_admin_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.connector import ConnectorOut
from app.schemas.outbound import EmailSendRequestCreate, EmailSendRequestDecision, EmailSendRequestOut, OutboundEmailPolicyOut, OutboundEmailPolicyUpdate, SmtpConnectorCreateRequest
from app.services.connector_service import connector_service
from app.services.outbound_email_service import outbound_email_service

router = APIRouter()


@router.get("/policy", response_model=OutboundEmailPolicyOut)
def get_policy(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    policy = outbound_email_service._policy(db=db, organization_id=current_user.organization_id)
    return OutboundEmailPolicyOut(**outbound_email_service.serialize_policy(policy, current_user.organization_id))


@router.put("/policy", response_model=OutboundEmailPolicyOut)
def update_policy(req: OutboundEmailPolicyUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    policy = outbound_email_service.update_policy(db=db, user=current_user, request=req)
    return OutboundEmailPolicyOut(**outbound_email_service.serialize_policy(policy, current_user.organization_id))


@router.post("/smtp-connectors", response_model=ConnectorOut)
def create_smtp_connector(req: SmtpConnectorCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        connector = outbound_email_service.create_smtp_connector(db=db, user=current_user, request=req)
        return ConnectorOut(**connector_service.serialize_connector(connector))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(400, "SMTP 连接创建失败", code="SMTP_CONNECTOR_CREATE_FAILED", detail=str(exc))


@router.get("/smtp-connectors", response_model=list[ConnectorOut])
def list_smtp_connectors(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [ConnectorOut(**connector_service.serialize_connector(item)) for item in outbound_email_service.list_smtp_connectors(db=db, user=current_user)]


@router.post("/drafts/{draft_id}/send-requests", response_model=EmailSendRequestOut)
def request_send(draft_id: int, req: EmailSendRequestCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        row = outbound_email_service.request_send(draft_id, connector_id=req.smtp_connector_id, db=db, user=current_user)
        return EmailSendRequestOut(**outbound_email_service.serialize_request(row, db=db, viewer=current_user))
    except ValueError as exc:
        raise api_error(400, "发送申请失败", code="EMAIL_SEND_REQUEST_INVALID", detail=str(exc))


@router.get("/send-requests", response_model=list[EmailSendRequestOut])
def list_send_requests(draft_id: int | None = Query(None), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [EmailSendRequestOut(**outbound_email_service.serialize_request(row, db=db, viewer=current_user)) for row in outbound_email_service.list_requests(db=db, user=current_user, draft_id=draft_id)]


@router.post("/send-requests/{request_id}/decision", response_model=EmailSendRequestOut)
def decide_send_request(request_id: int, req: EmailSendRequestDecision, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        row = outbound_email_service.decide_request(request_id, approved=req.approved, note=req.note, db=db, user=current_user)
        return EmailSendRequestOut(**outbound_email_service.serialize_request(row, db=db, viewer=current_user))
    except ValueError as exc:
        raise api_error(400, "发送审批失败", code="EMAIL_SEND_DECISION_INVALID", detail=str(exc))


@router.post("/send-requests/{request_id}/execute", response_model=EmailSendRequestOut)
def execute_send_request(request_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        row = outbound_email_service.execute_request(request_id, db=db, user=current_user)
        return EmailSendRequestOut(**outbound_email_service.serialize_request(row, db=db, viewer=current_user))
    except ValueError as exc:
        raise api_error(400, "邮件发送失败", code="EMAIL_SEND_EXECUTE_INVALID", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(502, "SMTP 发送失败", code="SMTP_SEND_FAILED", detail=str(exc))
