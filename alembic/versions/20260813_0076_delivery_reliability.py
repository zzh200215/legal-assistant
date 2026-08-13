"""P0/P1 通知/邮件/外发可靠投递：Outbox 投递列、模板、附件、邮箱同步。

- legal_notification_events 补投递可靠性列：attempt/max_attempts/next_retry_at/
  claimed_by/claim_expires_at/error_code/sanitized_error_message/provider_message_id/
  email_send_request_id/template_key/template_version/locale/idempotency_key(UNIQUE)。
- email_send_requests 补投递列 + bcc + notification_event_id（邮件 Outbox 语义）。
- 新表 notification_templates：channel+template_key+locale+version 唯一，版本不可覆盖。
- 新表 email_attachments：附件 DLP/安全台账。
- 新表 mailbox_sync_accounts / mailbox_messages / mailbox_attachments：UID 幂等 + 附件安全。
- 回填：既有通知事件补 legacy 幂等键与默认计数（幂等，不臆造数据）。

Revision ID: 20260813_0076
Revises: 20260813_0075
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "20260813_0076"
down_revision = "20260813_0075"
branch_labels = None
depends_on = None


# ── legal_notification_events 扩展 ─────────────────────────────────────────────

def _extend_legal_notification_events() -> None:
    with op.batch_alter_table("legal_notification_events") as batch_op:
        batch_op.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("max_attempts", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("claimed_by", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("sanitized_error_message", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("provider_message_id", sa.String(256), nullable=True))
        batch_op.add_column(sa.Column("email_send_request_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("template_key", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("template_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("locale", sa.String(16), nullable=True))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
        batch_op.create_index("ix_legal_notification_events_next_retry_at",
                              ["next_retry_at"])
        batch_op.create_index("ix_legal_notification_events_claim_expires_at",
                              ["claim_expires_at"])
        batch_op.create_index("ix_legal_notification_events_idempotency_key",
                              ["idempotency_key"])
        batch_op.create_index("ix_legal_notification_events_email_send_request_id",
                              ["email_send_request_id"])
        batch_op.create_index("ix_legal_notification_events_status_next_retry_claim",
                              ["status", "next_retry_at", "claim_expires_at"])
        batch_op.create_index("uq_legal_notification_events_idempotency_key",
                              ["idempotency_key"], unique=True)
    op.create_foreign_key(
        "fk_legal_notification_events_email_send_request",
        "legal_notification_events", "email_send_requests",
        ["email_send_request_id"], ["id"],
    )


# ── email_send_requests 扩展 ───────────────────────────────────────────────────

def _extend_email_send_requests() -> None:
    with op.batch_alter_table("email_send_requests") as batch_op:
        batch_op.add_column(sa.Column("bcc", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("max_attempts", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("claimed_by", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("sanitized_error_message", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("dead_letter_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("dead_letter_reason", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("notification_event_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_email_send_requests_next_retry_at", ["next_retry_at"])
        batch_op.create_index("ix_email_send_requests_claim_expires_at", ["claim_expires_at"])
        batch_op.create_index("ix_email_send_requests_notification_event_id",
                              ["notification_event_id"])
        batch_op.create_index("ix_email_send_requests_status_next_retry_claim",
                              ["status", "next_retry_at", "claim_expires_at"])
    op.create_foreign_key(
        "fk_email_send_requests_notification_event",
        "email_send_requests", "legal_notification_events",
        ["notification_event_id"], ["id"],
    )


# ── notification_templates ─────────────────────────────────────────────────────

def _create_notification_templates() -> None:
    op.create_table(
        "notification_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("template_key", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("locale", sa.String(16), nullable=False, server_default="default"),
        sa.Column("subject_template", sa.String(512), nullable=True),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("params_schema_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("channel", "template_key", "locale", "version",
                            name="uq_notification_templates_channel_key_locale_version"),
    )
    op.create_index("ix_notification_templates_channel", "notification_templates", ["channel"])
    op.create_index("ix_notification_templates_template_key", "notification_templates", ["template_key"])
    op.create_index("ix_notification_templates_status", "notification_templates", ["status"])


# ── email_attachments ──────────────────────────────────────────────────────────

def _create_email_attachments() -> None:
    op.create_table(
        "email_attachments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("send_request_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("scan_status", sa.String(32), nullable=False, server_default="not_scanned"),
        sa.Column("scan_result_json", sa.Text(), nullable=True),
        sa.Column("scan_scanner_version", sa.String(64), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["draft_id"], ["email_drafts.id"]),
        sa.ForeignKeyConstraint(["send_request_id"], ["email_send_requests.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
    )
    op.create_index("ix_email_attachments_draft_id", "email_attachments", ["draft_id"])
    op.create_index("ix_email_attachments_send_request_id", "email_attachments", ["send_request_id"])
    op.create_index("ix_email_attachments_content_hash", "email_attachments", ["content_hash"])
    op.create_index("ix_email_attachments_scan_status", "email_attachments", ["scan_status"])


# ── mailbox 三表 ───────────────────────────────────────────────────────────────

def _create_mailbox_tables() -> None:
    op.create_table(
        "mailbox_sync_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("connector_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email_address", sa.String(256), nullable=False),
        sa.Column("imap_host", sa.String(256), nullable=True),
        sa.Column("uidvalidity", sa.String(64), nullable=True),
        sa.Column("last_successful_uid", sa.String(128), nullable=True),
        sa.Column("cursor_json", sa.Text(), nullable=True),
        sa.Column("checkpoint_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("sanitized_error_message", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("connector_id", name="uq_mailbox_sync_accounts_connector_id"),
        sa.ForeignKeyConstraint(["connector_id"], ["external_connectors.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_mailbox_sync_accounts_connector_id", "mailbox_sync_accounts", ["connector_id"])
    op.create_index("ix_mailbox_sync_accounts_uidvalidity", "mailbox_sync_accounts", ["uidvalidity"])
    op.create_index("ix_mailbox_sync_accounts_next_retry_at", "mailbox_sync_accounts", ["next_retry_at"])
    op.create_index("ix_mailbox_sync_accounts_claim_expires_at", "mailbox_sync_accounts", ["claim_expires_at"])

    op.create_table(
        "mailbox_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("folder", sa.String(256), nullable=False, server_default="INBOX"),
        sa.Column("uidvalidity", sa.String(64), nullable=False),
        sa.Column("uid", sa.String(128), nullable=False),
        sa.Column("message_id", sa.String(256), nullable=True),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("sender", sa.String(512), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("has_attachments", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attachment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("process_result", sa.String(64), nullable=True),
        sa.Column("process_error_code", sa.String(64), nullable=True),
        sa.Column("sync_run_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("account_id", "folder", "uidvalidity", "uid",
                            name="uq_mailbox_messages_account_folder_uidvalidity_uid"),
        sa.ForeignKeyConstraint(["account_id"], ["mailbox_sync_accounts.id"]),
    )
    op.create_index("ix_mailbox_messages_account_id", "mailbox_messages", ["account_id"])
    op.create_index("ix_mailbox_messages_message_id", "mailbox_messages", ["message_id"])
    op.create_index("ix_mailbox_messages_sync_run_id", "mailbox_messages", ["sync_run_id"])

    op.create_table(
        "mailbox_attachments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("scan_status", sa.String(32), nullable=False, server_default="not_scanned"),
        sa.Column("scan_result_json", sa.Text(), nullable=True),
        sa.Column("scan_scanner_version", sa.String(64), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("process_status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["message_id"], ["mailbox_messages.id"]),
        sa.ForeignKeyConstraint(["account_id"], ["mailbox_sync_accounts.id"]),
    )
    op.create_index("ix_mailbox_attachments_message_id", "mailbox_attachments", ["message_id"])
    op.create_index("ix_mailbox_attachments_content_hash", "mailbox_attachments", ["content_hash"])
    op.create_index("ix_mailbox_attachments_scan_status", "mailbox_attachments", ["scan_status"])


# ── 回填 ───────────────────────────────────────────────────────────────────────

def _backfill(bind) -> None:
    """既有通知事件补 legacy 幂等键与默认计数（幂等，不臆造数据）。

    ``bind`` 可能是 engine（测试直调）或 Connection（``op.get_bind()`` 迁移期）。
    """
    from sqlalchemy import Connection, text

    def _run(conn) -> None:
        events = sa.table(
            "legal_notification_events",
            sa.column("id", sa.Integer),
            sa.column("idempotency_key", sa.String(128)),
            sa.column("attempt", sa.Integer),
            sa.column("max_attempts", sa.Integer),
        )
        conn.execute(
            sa.update(events)
            .where(events.c.idempotency_key.is_(None))
            .values(idempotency_key=sa.func.concat("legacy:notify:", events.c.id))
        )
        conn.execute(text(
            "UPDATE legal_notification_events SET attempt = 0 WHERE attempt IS NULL"
        ))
        conn.execute(text(
            "UPDATE email_send_requests SET attempt = 0 WHERE attempt IS NULL"
        ))

    if isinstance(bind, Connection):
        _run(bind)
    else:
        with bind.connect() as conn:
            _run(conn)
            conn.commit()


def upgrade() -> None:
    _extend_legal_notification_events()
    _extend_email_send_requests()
    _create_notification_templates()
    _create_email_attachments()
    _create_mailbox_tables()
    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_table("mailbox_attachments")
    op.drop_table("mailbox_messages")
    op.drop_table("mailbox_sync_accounts")
    op.drop_table("email_attachments")
    op.drop_table("notification_templates")
    with op.batch_alter_table("email_send_requests") as batch_op:
        batch_op.drop_index("ix_email_send_requests_status_next_retry_claim")
        batch_op.drop_index("ix_email_send_requests_notification_event_id")
        batch_op.drop_index("ix_email_send_requests_claim_expires_at")
        batch_op.drop_index("ix_email_send_requests_next_retry_at")
        batch_op.drop_column("notification_event_id")
        batch_op.drop_column("dead_letter_reason")
        batch_op.drop_column("dead_letter_at")
        batch_op.drop_column("sanitized_error_message")
        batch_op.drop_column("error_code")
        batch_op.drop_column("claim_expires_at")
        batch_op.drop_column("claimed_by")
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("attempt")
        batch_op.drop_column("bcc")
    with op.batch_alter_table("legal_notification_events") as batch_op:
        batch_op.drop_index("uq_legal_notification_events_idempotency_key")
        batch_op.drop_index("ix_legal_notification_events_status_next_retry_claim")
        batch_op.drop_index("ix_legal_notification_events_email_send_request_id")
        batch_op.drop_index("ix_legal_notification_events_idempotency_key")
        batch_op.drop_index("ix_legal_notification_events_claim_expires_at")
        batch_op.drop_index("ix_legal_notification_events_next_retry_at")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("locale")
        batch_op.drop_column("template_version")
        batch_op.drop_column("template_key")
        batch_op.drop_column("email_send_request_id")
        batch_op.drop_column("provider_message_id")
        batch_op.drop_column("sanitized_error_message")
        batch_op.drop_column("error_code")
        batch_op.drop_column("claim_expires_at")
        batch_op.drop_column("claimed_by")
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("max_attempts")
        batch_op.drop_column("attempt")
