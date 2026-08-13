import json

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.api_response import api_error, should_passthrough_exception
from app.core.auth import get_current_user, require_admin_user
from app.core.database import get_db
from app.models.connector import ExternalConnector
from app.models.user import User
from app.schemas.connector import ConnectorOut
from app.schemas.outbound import EmailSendRequestCreate, EmailSendRequestDecision, EmailSendRequestOut, OutboundEmailPolicyOut, OutboundEmailPolicyUpdate, SmtpConnectorCreateRequest
from app.services.document_security import DocumentSecurityError
from app.services.outbound_email_service import outbound_email_service

router = APIRouter()


def _serialize_smtp_connector(connector: ExternalConnector) -> dict:
    config: dict = {}
    try:
        parsed = json.loads(connector.config_json or "{}")
        if isinstance(parsed, dict):
            config = parsed
    except (TypeError, ValueError):
        config = {}
    for key in ("password", "authorization_code", "access_token", "refresh_token", "client_secret"):
        config.pop(key, None)
    return {
        "id": connector.id,
        "user_id": connector.user_id,
        "organization_id": connector.organization_id,
        "department_id": connector.department_id,
        "connector_type": connector.connector_type,
        "name": connector.name,
        "status": connector.status,
        "config_json": json.dumps(config, ensure_ascii=False) if config else None,
        "last_sync_at": None,
        "last_sync_status": None,
        "last_imported_count": 0,
        "last_skipped_count": 0,
        "total_imported_count": 0,
        "total_skipped_count": 0,
        "created_at": connector.created_at,
        "updated_at": connector.updated_at,
    }


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
        return ConnectorOut(**{**_serialize_smtp_connector(connector), "last_sync_at": None})
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(400, "SMTP 连接创建失败", code="SMTP_CONNECTOR_CREATE_FAILED", detail=str(exc))


@router.get("/smtp-connectors", response_model=list[ConnectorOut])
def list_smtp_connectors(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [ConnectorOut(**_serialize_smtp_connector(item)) for item in outbound_email_service.list_smtp_connectors(db=db, user=current_user)]


@router.post("/drafts/{draft_id}/send-requests", response_model=EmailSendRequestOut)
def request_send(draft_id: int, req: EmailSendRequestCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        row = outbound_email_service.request_send(draft_id, connector_id=req.smtp_connector_id, db=db, user=current_user)
        return EmailSendRequestOut(**outbound_email_service.serialize_request(row, db=db, viewer=current_user))
    except ValueError as exc:
        raise api_error(400, "发送申请失败", code="EMAIL_SEND_REQUEST_INVALID", detail=str(exc))


@router.post("/drafts/{draft_id}/attachments")
def upload_draft_attachment(draft_id: int, file: UploadFile = File(...),
                            db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """上传邮件草稿附件：流式安全处理 + DLP 硬门禁，blocked 附件直接拒绝。"""
    try:
        row = outbound_email_service.upload_attachment(db=db, user=current_user, draft_id=draft_id, file=file)
        return {
            "id": row.id,
            "draft_id": row.draft_id,
            "filename": row.filename,
            "mime_type": row.mime_type,
            "size_bytes": row.size_bytes,
            "content_hash": row.content_hash,
            "scan_status": row.scan_status,
        }
    except DocumentSecurityError as exc:
        raise api_error(400, exc.message, code=exc.code)
    except ValueError as exc:
        raise api_error(400, "附件上传失败", code="ATTACHMENT_UPLOAD_INVALID", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "附件上传失败", code="ATTACHMENT_UPLOAD_FAILED", detail=str(exc))


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
