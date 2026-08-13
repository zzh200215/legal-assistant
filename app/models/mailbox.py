"""邮箱同步模型（绿地，mock 连接器；不复活真实 IMAP 供应商）。

可靠幂等与安全处理：
- 唯一标识 = account_id + folder + UIDVALIDITY + UID；Message-ID 仅辅助去重，
  不作为唯一可靠边界。
- cursor 仅在整批邮件及其关联数据成功持久化后推进（SyncRun.cursor_json）。
- 附件流式保存 + 大小/MIME 白名单/哈希去重；扫描失败或高风险附件隔离。
- 不记录邮件正文、附件内容、访问令牌到普通日志。
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class MailboxSyncAccount(Base):
    """邮箱同步账户：连接器 + 同步游标（UIDVALIDITY / last UID）。

    与 connector_sync_framework 共用 SyncRun（connector_sync_jobs）台账，
    一个账户对应一个 ExternalConnector（connector_type=mailbox_imap）。
    """
    __tablename__ = "mailbox_sync_accounts"
    __table_args__ = (
        UniqueConstraint("connector_id", name="uq_mailbox_sync_accounts_connector_id"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    connector_id = Column(Integer, ForeignKey("external_connectors.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    email_address = Column(String(256), nullable=False, comment="账户邮箱，脱敏展示")
    imap_host = Column(String(256), nullable=True, comment="仅 mock；真实 IMAP 不接入")
    # 同步断点：UIDVALIDITY + last_successful_uid，仅在整批成功提交后推进
    uidvalidity = Column(String(64), nullable=True, index=True, comment="IMAP UIDVALIDITY 或等价游标")
    last_successful_uid = Column(String(128), nullable=True, comment="最后成功处理的 UID")
    cursor_json = Column(Text, nullable=True, comment="已提交游标（整批成功后推进）")
    checkpoint_json = Column(Text, nullable=True, comment="批内 checkpoint（下批起点）")
    status = Column(String(32), nullable=False, default="active", index=True,
                    comment="active / paused / running / error")
    error_code = Column(String(64), nullable=True)
    sanitized_error_message = Column(Text, nullable=True)
    attempt = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    claimed_by = Column(String(128), nullable=True, comment="持有同步的 worker/run 标识")
    claim_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MailboxMessage(Base):
    """同步到的邮件消息镜像：幂等 upsert，重复同步不重复创建通知/附件/任务。

    UNIQUE(account_id, folder, uidvalidity, uid)：同一账户/文件夹/UIDVALIDITY/UID
    只保留一行；Message-ID 仅辅助去重。
    """
    __tablename__ = "mailbox_messages"
    __table_args__ = (
        UniqueConstraint("account_id", "folder", "uidvalidity", "uid",
                         name="uq_mailbox_messages_account_folder_uidvalidity_uid"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("mailbox_sync_accounts.id"), nullable=False, index=True)
    folder = Column(String(256), nullable=False, default="INBOX")
    uidvalidity = Column(String(64), nullable=False, comment="IMAP UIDVALIDITY 或等价游标")
    uid = Column(String(128), nullable=False, comment="IMAP UID 或等价稳定 provider id")
    message_id = Column(String(256), nullable=True, index=True, comment="Message-ID，仅辅助去重")
    subject = Column(String(512), nullable=False)
    sender = Column(String(512), nullable=True, comment="发件人，脱敏存储")
    received_at = Column(DateTime(timezone=True), nullable=True)
    content_hash = Column(String(64), nullable=False, comment="正文+附件清单哈希，幂等跳过")
    has_attachments = Column(Integer, nullable=False, default=0)
    attachment_count = Column(Integer, nullable=False, default=0)
    processed = Column(Integer, nullable=False, default=0, comment="是否已进入下游处理（通知/文档）")
    process_result = Column(String(64), nullable=True, comment="success / skipped / failed / quarantined")
    process_error_code = Column(String(64), nullable=True)
    sync_run_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class MailboxAttachment(Base):
    """同步邮件的附件台账：流式保存 + 安全扫描结果 + 隔离状态。

    扫描失败或高风险附件置 quarantined，不进入文档解析/通知流程。
    """
    __tablename__ = "mailbox_attachments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    message_id = Column(Integer, ForeignKey("mailbox_messages.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("mailbox_sync_accounts.id"), nullable=False, index=True)
    filename = Column(String(512), nullable=False)
    mime_type = Column(String(128), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    content_hash = Column(String(64), nullable=False, index=True)
    storage_key = Column(String(512), nullable=True, comment="对象存储 key")
    scan_status = Column(String(32), nullable=False, default="not_scanned", index=True,
                         comment="not_scanned / clean / blocked / quarantined / error")
    scan_result_json = Column(Text, nullable=True, comment="脱敏命中摘要")
    scan_scanner_version = Column(String(64), nullable=True)
    scanned_at = Column(DateTime(timezone=True), nullable=True)
    process_status = Column(String(32), nullable=False, default="pending",
                            comment="pending / imported / quarantined / skipped")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
