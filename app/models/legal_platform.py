"""Phase 13 — 开放平台：developer_apps / api_keys / api_usage / webhooks / legal_async_jobs"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base
from app.core.encryption import EncryptedText


class DeveloperApp(Base):
    __tablename__ = "developer_apps"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False, comment="应用名称，组织内唯一")
    status = Column(String(16), nullable=False, default="active", index=True,
                    comment="active / disabled")
    ip_whitelist_json = Column(Text, nullable=True, comment="IP白名单CIDR列表JSON数组")
    webhook_url = Column(String(512), nullable=True, comment="Webhook回调地址，必须HTTPS")
    webhook_secret_hash = Column(String(64), nullable=True, comment="签名密钥哈希")
    subscribed_events_json = Column(Text, nullable=True, comment="订阅事件类型JSON数组")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DeveloperApiKey(Base):
    __tablename__ = "developer_api_keys"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    app_id = Column(Integer, ForeignKey("developer_apps.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    key_hash = Column(String(64), nullable=False, unique=True, index=True, comment="SHA-256哈希")
    key_prefix = Column(String(12), nullable=False, comment="前缀标识，如'lzj_op_xxxx'")
    status = Column(String(16), nullable=False, default="active", comment="active / revoked")
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    transition_until = Column(DateTime(timezone=True), nullable=True, comment="轮换旧密钥过渡期截止")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class DeveloperApiUsage(Base):
    __tablename__ = "developer_api_usage"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    app_id = Column(Integer, ForeignKey("developer_apps.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    endpoint = Column(String(256), nullable=False)
    method = Column(String(8), nullable=False)
    status_code = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    tokens_used = Column(Integer, nullable=True, default=0)
    stat_date = Column(String(10), nullable=False, index=True, comment="统计日期 YYYY-MM-DD")
    stat_hour = Column(Integer, nullable=True, comment="统计小时 0-23")
    call_count = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WebhookSubscription(Base):
    __tablename__ = "webhook_subscriptions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    app_id = Column(Integer, ForeignKey("developer_apps.id", ondelete="CASCADE"),
                    nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True,
                        comment="contract_review.completed / approval.status_changed / ...")
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    subscription_id = Column(Integer, ForeignKey("webhook_subscriptions.id"), nullable=True, index=True)
    app_id = Column(Integer, ForeignKey("developer_apps.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    event_id = Column(String(128), nullable=False, unique=True, index=True, comment="幂等事件ID")
    payload_hash = Column(String(64), nullable=True, comment="投递载荷哈希，不存原始载荷")
    status = Column(String(16), nullable=False, default="pending", index=True,
                    comment="pending / success / failed")
    attempt_count = Column(Integer, nullable=False, default=0)
    last_attempted_at = Column(DateTime(timezone=True), nullable=True)
    response_status = Column(Integer, nullable=True)
    response_body_snippet = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalAsyncJob(Base):
    __tablename__ = "legal_async_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    resource_type = Column(String(64), nullable=True, comment="contract / invoice / sign_request / ...")
    resource_id = Column(Integer, nullable=True, index=True)
    job_type = Column(String(64), nullable=False, index=True,
                      comment="contract_parse / contract_diff / invoice_pdf / expiry_scan / export / ...")
    status = Column(String(16), nullable=False, default="queued", index=True,
                    comment="queued / processing / succeeded / failed / cancelled")
    idempotency_key = Column(String(128), nullable=True, unique=True, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    progress = Column(Integer, nullable=True, comment="进度百分比 0-100")
    error_summary = Column(Text, nullable=True, comment="脱敏后的错误摘要，不含敏感数据")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    result_summary = Column(Text, nullable=True, comment="结果摘要，不含合同正文/密钥/令牌")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalAsyncJobInput(Base):
    """开放接口的受控输入；正文加密，任务表仅保留脱敏结果。"""
    __tablename__ = "legal_async_job_inputs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("legal_async_jobs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    app_id = Column(Integer, ForeignKey("developer_apps.id"), nullable=False, index=True)
    request_fingerprint = Column(String(64), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    content_ciphertext = Column(EncryptedText, nullable=False)
    contract_type = Column(String(64), nullable=True)
    review_policy_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
