from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.api_response import api_error, should_passthrough_exception
from app.core.auth import get_current_user, require_admin_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.connector import (
    ConnectorCredentialRotateRequest,
    ConnectorCreateRequest,
    ConnectorOut,
    ConnectorSyncJobOut,
    ConnectorSyncRequest,
    EnterpriseConnectorCreateRequest,
    EnterpriseConnectorCredentialRequest,
    MicrosoftOAuthStartRequest,
    MicrosoftOAuthStartResponse,
)
from app.services.connector_service import connector_service

router = APIRouter()


@router.get("/", response_model=list[ConnectorOut])
def list_connectors(
    include_all: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = connector_service.list_connectors(db=db, user=current_user, include_all=include_all)
    return [ConnectorOut(**connector_service.serialize_connector(row)) for row in rows]


@router.post("/", response_model=ConnectorOut)
def create_connector(
    req: ConnectorCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = connector_service.create_connector(
            db=db,
            user=current_user,
            connector_type=req.connector_type,
            name=req.name,
            config_json=req.config_json,
        )
        return ConnectorOut(**connector_service.serialize_connector(row))
    except ValueError as exc:
        raise api_error(400, "连接器配置不合法", code="CONNECTOR_CREATE_INVALID", detail=str(exc))
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "连接器创建失败", code="CONNECTOR_CREATE_FAILED", detail=str(e))


@router.post("/enterprise", response_model=ConnectorOut)
def create_enterprise_connector(
    req: EnterpriseConnectorCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    """创建 Microsoft Graph、ERP 或 CRM 的受控企业连接器。"""
    try:
        row = connector_service.create_enterprise_connector(
            db=db,
            user=current_user,
            connector_type=req.connector_type,
            name=req.name,
            config=req.config,
            credentials=req.credentials,
        )
        return ConnectorOut(**connector_service.serialize_connector(row))
    except ValueError as exc:
        raise api_error(400, "企业连接器配置不合法", code="ENTERPRISE_CONNECTOR_CREATE_INVALID", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "企业连接器创建失败", code="ENTERPRISE_CONNECTOR_CREATE_FAILED", detail=str(exc))


@router.post("/{connector_id}/microsoft-oauth/start", response_model=MicrosoftOAuthStartResponse)
def start_microsoft_oauth(
    connector_id: int,
    req: MicrosoftOAuthStartRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        payload = connector_service.start_microsoft_oauth(
            db=db,
            user=current_user,
            connector_id=connector_id,
            redirect_uri=req.redirect_uri,
        )
        return MicrosoftOAuthStartResponse(**payload)
    except ValueError as exc:
        raise api_error(400, "Microsoft 授权无法开始", code="MICROSOFT_OAUTH_START_INVALID", detail=str(exc))


@router.get("/microsoft/callback", response_class=HTMLResponse, include_in_schema=False)
def microsoft_oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """Microsoft redirects here after administrator consent; state binds the request to one connector."""
    if error:
        return HTMLResponse(_oauth_callback_page(False, "Microsoft 授权未完成，请返回系统中心重试。"), status_code=400)
    if not code or not state:
        return HTMLResponse(_oauth_callback_page(False, "授权回调缺少必要参数。"), status_code=400)
    try:
        connector_service.complete_microsoft_oauth(db=db, code=code, state=state)
    except ValueError as exc:
        return HTMLResponse(_oauth_callback_page(False, str(exc)), status_code=400)
    except Exception:
        return HTMLResponse(_oauth_callback_page(False, "令牌交换失败，请检查 Azure 应用配置后重试。"), status_code=502)
    return HTMLResponse(_oauth_callback_page(True, "Microsoft 授权已完成，可关闭此窗口并返回系统中心同步。"))


def _oauth_callback_page(success: bool, message: str) -> str:
    color = "#167c52" if success else "#b42318"
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>企业连接器授权</title>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'></head>"
        "<body style='margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#f6f8fb;color:#182230'>"
        "<main style='max-width:560px;margin:12vh auto;padding:32px;background:#fff;border:1px solid #dbe3ef'>"
        f"<h1 style='margin:0 0 16px;font-size:22px;color:{color}'>企业连接器授权</h1>"
        f"<p style='line-height:1.7'>{message}</p></main></body></html>"
    )


@router.post("/{connector_id}/sync", response_model=ConnectorSyncJobOut)
def sync_connector(
    connector_id: int,
    req: ConnectorSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = connector_service.create_sync_job(
            db=db,
            connector_id=connector_id,
            user=current_user,
            sync_mode=req.sync_mode,
        )
        return ConnectorSyncJobOut.model_validate(row)
    except ValueError as exc:
        raise api_error(404, "连接器不存在", code="CONNECTOR_NOT_FOUND", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "连接器同步提交失败", code="CONNECTOR_SYNC_FAILED", detail=str(exc))


@router.post("/{connector_id}/credentials/rotate", response_model=ConnectorOut)
def rotate_connector_credentials(
    connector_id: int,
    req: ConnectorCredentialRotateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = connector_service.rotate_credentials(
            db=db, connector_id=connector_id, user=current_user,
            username=req.username, password=req.password,
        )
        return ConnectorOut(**connector_service.serialize_connector(row))
    except ValueError as exc:
        raise api_error(400, "连接器凭据轮换失败", code="CONNECTOR_CREDENTIAL_ROTATION_INVALID", detail=str(exc))


@router.put("/{connector_id}/enterprise-credentials", response_model=ConnectorOut)
def update_enterprise_connector_credentials(
    connector_id: int,
    req: EnterpriseConnectorCredentialRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        row = connector_service.update_enterprise_credentials(
            db=db,
            user=current_user,
            connector_id=connector_id,
            credentials=req.credentials,
        )
        return ConnectorOut(**connector_service.serialize_connector(row))
    except ValueError as exc:
        raise api_error(400, "企业连接器凭据更新失败", code="ENTERPRISE_CONNECTOR_CREDENTIAL_INVALID", detail=str(exc))


@router.post("/{connector_id}/disable", response_model=ConnectorOut)
def disable_connector(
    connector_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = connector_service.disable_connector(db=db, connector_id=connector_id, user=current_user)
        return ConnectorOut(**connector_service.serialize_connector(row))
    except ValueError as exc:
        raise api_error(400, "连接器停用失败", code="CONNECTOR_DISABLE_INVALID", detail=str(exc))


@router.get("/sync-jobs", response_model=list[ConnectorSyncJobOut])
def list_connector_sync_jobs(
    connector_id: int | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = connector_service.list_sync_jobs(db=db, user=current_user, connector_id=connector_id, status=status)
    return [ConnectorSyncJobOut.model_validate(row) for row in rows]
