from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SmtpConnectorCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=512)
    from_address: str = Field(min_length=3, max_length=255)
    use_starttls: bool = True


class OutboundEmailPolicyUpdate(BaseModel):
    enabled: bool
    allowed_recipient_domains: list[str] = Field(default_factory=list, max_length=200)
    max_sends_per_hour: int = Field(default=20, ge=1, le=500)
    require_approval: bool = True
    dlp_enabled: bool = True
    dlp_action: str = Field(default="block", pattern="^(block|warn)$")


class OutboundEmailPolicyOut(BaseModel):
    id: int | None = None
    organization_id: int | None = None
    enabled: bool = False
    allowed_recipient_domains: list[str] = Field(default_factory=list)
    max_sends_per_hour: int = 20
    require_approval: bool = True
    dlp_enabled: bool = True
    dlp_action: str = "block"
    updated_at: datetime | None = None


class EmailSendRequestCreate(BaseModel):
    smtp_connector_id: int


class EmailSendRequestDecision(BaseModel):
    approved: bool
    note: str | None = Field(default=None, max_length=1000)


class EmailSendRequestOut(BaseModel):
    id: int
    draft_id: int
    smtp_connector_id: int
    user_id: int
    requester_username: str | None = None
    recipient: str
    cc: str | None = None
    subject: str
    status: str
    approved_at: datetime | None = None
    approved_by_user_id: int | None = None
    approver_username: str | None = None
    rejection_note: str | None = None
    sent_at: datetime | None = None
    provider_message_id: str | None = None
    error_message: str | None = None
    dlp_status: str = "not_scanned"
    dlp_findings: list[dict] = Field(default_factory=list)
    dlp_scanned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    can_decide: bool = False
    can_execute: bool = False

    model_config = ConfigDict(from_attributes=True)
