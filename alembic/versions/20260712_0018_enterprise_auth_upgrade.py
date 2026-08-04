"""企业账号与权限体系升级

Revision ID: 20260712_0018
Revises: 20260708_0017
Create Date: 2026-07-12

新增：
- 用户状态、登录安全字段
- 外部账号关联字段
- 登录日志表
- 管理员审计日志表
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260712_0018"
down_revision: Union[str, None] = "20260708_0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 用户表新增字段
    op.add_column("users", sa.Column("status", sa.String(32), nullable=False, server_default="active"))
    op.create_index("ix_users_status", "users", ["status"])

    op.add_column("users", sa.Column("employee_id", sa.String(64), nullable=True))
    op.create_index("ix_users_employee_id", "users", ["employee_id"])

    op.add_column("users", sa.Column("external_provider", sa.String(32), nullable=True))
    op.create_index("ix_users_external_provider", "users", ["external_provider"])

    op.add_column("users", sa.Column("external_user_id", sa.String(128), nullable=True))
    op.create_index("ix_users_external_user_id", "users", ["external_user_id"])

    op.add_column("users", sa.Column("login_fail_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("last_login_ip", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("force_password_change", sa.Boolean(), nullable=False, server_default="0"))

    # 2. 登录日志表
    op.create_table(
        "login_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_foreign_key("fk_login_logs_user_id", "login_logs", "users", ["user_id"], ["id"])
    op.create_index("ix_login_logs_user_id", "login_logs", ["user_id"])
    op.create_index("ix_login_logs_username", "login_logs", ["username"])
    op.create_index("ix_login_logs_event_type", "login_logs", ["event_type"])
    op.create_index("ix_login_logs_created_at", "login_logs", ["created_at"])

    # 3. 管理员审计日志表
    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("operator_id", sa.Integer(), nullable=False),
        sa.Column("operator_name", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("target_name", sa.String(128), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_foreign_key("fk_admin_audit_logs_operator_id", "admin_audit_logs", "users", ["operator_id"], ["id"])
    op.create_index("ix_admin_audit_logs_operator_id", "admin_audit_logs", ["operator_id"])
    op.create_index("ix_admin_audit_logs_action", "admin_audit_logs", ["action"])
    op.create_index("ix_admin_audit_logs_target_type", "admin_audit_logs", ["target_type"])
    op.create_index("ix_admin_audit_logs_target_id", "admin_audit_logs", ["target_id"])
    op.create_index("ix_admin_audit_logs_created_at", "admin_audit_logs", ["created_at"])


def downgrade() -> None:
    # 审计日志表
    op.drop_index("ix_admin_audit_logs_created_at", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_target_id", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_target_type", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_action", table_name="admin_audit_logs")
    op.drop_index("ix_admin_audit_logs_operator_id", table_name="admin_audit_logs")
    op.drop_constraint("fk_admin_audit_logs_operator_id", "admin_audit_logs", type_="foreignkey")
    op.drop_table("admin_audit_logs")

    # 登录日志表
    op.drop_index("ix_login_logs_created_at", table_name="login_logs")
    op.drop_index("ix_login_logs_event_type", table_name="login_logs")
    op.drop_index("ix_login_logs_username", table_name="login_logs")
    op.drop_index("ix_login_logs_user_id", table_name="login_logs")
    op.drop_constraint("fk_login_logs_user_id", "login_logs", type_="foreignkey")
    op.drop_table("login_logs")

    # 用户表字段
    op.drop_column("users", "force_password_change")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "last_login_ip")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "locked_until")
    op.drop_column("users", "login_fail_count")
    op.drop_index("ix_users_external_user_id", table_name="users")
    op.drop_column("users", "external_user_id")
    op.drop_index("ix_users_external_provider", table_name="users")
    op.drop_column("users", "external_provider")
    op.drop_index("ix_users_employee_id", table_name="users")
    op.drop_column("users", "employee_id")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_column("users", "status")