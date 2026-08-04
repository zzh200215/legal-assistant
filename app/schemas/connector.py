from datetime import datetime

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConnectorCreateRequest(BaseModel):
    connector_type: str
    name: str
    config_json: str | None = None


class ConnectorSyncRequest(BaseModel):
    sync_mode: str = "manual"


class ConnectorCredentialRotateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=512)


class EnterpriseConnectorCreateRequest(BaseModel):
    """管理员配置的企业数据源。凭据与可展示配置强制分开传递。"""

    connector_type: Literal[
        "ms_graph_onedrive",
        "ms_graph_sharepoint",
        "erp_rest",
        "crm_rest",
    ]
    name: str = Field(min_length=1, max_length=128)
    config: dict = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)


class EnterpriseConnectorCredentialRequest(BaseModel):
    credentials: dict[str, str] = Field(default_factory=dict)


class MicrosoftOAuthStartRequest(BaseModel):
    redirect_uri: str = Field(min_length=10, max_length=1024)


class MicrosoftOAuthStartResponse(BaseModel):
    authorize_url: str
    expires_at: datetime


class ConnectorOut(BaseModel):
    id: int
    user_id: int
    organization_id: int | None = None
    department_id: int | None = None
    connector_type: str
    name: str
    status: str
    config_json: str | None = None
    last_sync_at: datetime | None = None
    last_sync_status: str | None = None
    last_imported_count: int = 0
    last_skipped_count: int = 0
    total_imported_count: int = 0
    total_skipped_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConnectorSyncJobOut(BaseModel):
    id: int
    connector_id: int
    user_id: int
    status: str
    sync_mode: str
    result_summary: str | None = None
    result_detail_json: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
