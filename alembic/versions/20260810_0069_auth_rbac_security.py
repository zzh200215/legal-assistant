"""P0 认证安全：token 撤销/刷新、设备、MFA、授权快照表 + users.token_version。

- revoked_tokens：按 jti 撤销 access token。
- refresh_tokens：刷新令牌轮换（仅存哈希）与重放检测。
- auth_devices：确定性设备/IP 风险识别。
- mfa_credentials / mfa_challenges / mfa_recovery_codes：TOTP MFA（secret 加密，恢复码哈希）。
- authorization_snapshots：长流程权限快照。
- users.token_version：递增即失效全部旧 access token，兼容 SQLite / MySQL。

Revision ID: 20260810_0069
Revises: 20260809_0068
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "20260810_0069"
down_revision = "20260809_0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("jti", sa.String(128), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_type", sa.String(16), nullable=False, server_default="access"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("revoke_reason", sa.String(64), nullable=True),
    )
    op.create_index("ix_revoked_tokens_jti", "revoked_tokens", ["jti"], unique=True)
    op.create_index("ix_revoked_tokens_user_id", "revoked_tokens", ["user_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("user_agent_hash", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(64), nullable=True),
    )
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])

    op.create_table(
        "auth_devices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("device_name", sa.String(128), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
        sa.Column("user_agent_hash", sa.String(64), nullable=True),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
        sa.Column("risk_reason", sa.String(128), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_auth_devices_user_id", "auth_devices", ["user_id"])
    op.create_index("ix_auth_devices_device_id", "auth_devices", ["device_id"])
    op.create_index("ix_auth_devices_risk_level", "auth_devices", ["risk_level"])

    op.create_table(
        "mfa_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_mfa_credentials_user_id", "mfa_credentials", ["user_id"], unique=True)

    op.create_table(
        "mfa_challenges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("challenge_jti", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False, server_default="mfa_login"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_mfa_challenges_challenge_jti", "mfa_challenges", ["challenge_jti"], unique=True)
    op.create_index("ix_mfa_challenges_user_id", "mfa_challenges", ["user_id"])

    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_mfa_recovery_codes_code_hash", "mfa_recovery_codes", ["code_hash"], unique=True)
    op.create_index("ix_mfa_recovery_codes_user_id", "mfa_recovery_codes", ["user_id"])

    op.create_table(
        "authorization_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id"), nullable=True),
        sa.Column("legal_role", sa.String(32), nullable=True),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("jti", sa.String(128), nullable=True),
        sa.Column("resource_scope_json", sa.Text(), nullable=True),
        sa.Column("explicit_shares_json", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(64), nullable=True),
    )
    op.create_index("ix_authorization_snapshots_snapshot_id", "authorization_snapshots", ["snapshot_id"], unique=True)
    op.create_index("ix_authorization_snapshots_user_id", "authorization_snapshots", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_authorization_snapshots_user_id", table_name="authorization_snapshots")
    op.drop_index("ix_authorization_snapshots_snapshot_id", table_name="authorization_snapshots")
    op.drop_table("authorization_snapshots")
    op.drop_index("ix_mfa_recovery_codes_user_id", table_name="mfa_recovery_codes")
    op.drop_index("ix_mfa_recovery_codes_code_hash", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
    op.drop_index("ix_mfa_challenges_user_id", table_name="mfa_challenges")
    op.drop_index("ix_mfa_challenges_challenge_jti", table_name="mfa_challenges")
    op.drop_table("mfa_challenges")
    op.drop_index("ix_mfa_credentials_user_id", table_name="mfa_credentials")
    op.drop_table("mfa_credentials")
    op.drop_index("ix_auth_devices_risk_level", table_name="auth_devices")
    op.drop_index("ix_auth_devices_device_id", table_name="auth_devices")
    op.drop_index("ix_auth_devices_user_id", table_name="auth_devices")
    op.drop_table("auth_devices")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_revoked_tokens_user_id", table_name="revoked_tokens")
    op.drop_index("ix_revoked_tokens_jti", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
    op.drop_column("users", "token_version")
