"""P1 API·OpenAPI·WebSocket 统一化：幂等台账扩展列 / Job 取消标记 / WS 事件日志表。

- idempotency_keys 扩展（全部可空，存量行零迁移成本）：
  endpoint / user_id / organization_id / resource_id —— 幂等记录可审计维度，
  租户隔离仍由 scope 字符串 + organization_id 双保险。
  注：本表带唯一约束（uq_idempotency_keys_scope_key），sqlite batch 模式无法重建
  带约束表，故用原生 add_column/create_index（MySQL/sqlite 均支持）。
- legal_async_jobs.cancel_requested：取消请求标记（queued 直改 cancelled，
  processing 置标记由消费者检查；重复取消幂等）。
- 新表 ws_event_logs：WebSocket 会话持久化事件源（断线恢复用）。
  仅状态事件（job_update/notification/run_snapshot 等）落库；流式 chunk 标记
  volatile=1 不落库。resume_token 绑定 user/org/channel/过期时间。

Revision ID: 20261020_0081
Revises: 20260920_0080
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "20261020_0081"
down_revision = "20260920_0080"
branch_labels = None
depends_on = None


def _extend_idempotency_keys() -> None:
    op.add_column("idempotency_keys", sa.Column("endpoint", sa.String(128), nullable=True))
    op.add_column("idempotency_keys", sa.Column("user_id", sa.Integer(), nullable=True))
    op.add_column("idempotency_keys", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.add_column("idempotency_keys", sa.Column("resource_id", sa.String(64), nullable=True))
    op.create_index("ix_idempotency_keys_user_id", "idempotency_keys", ["user_id"])
    op.create_index("ix_idempotency_keys_organization_id", "idempotency_keys", ["organization_id"])


def _extend_legal_async_jobs() -> None:
    op.add_column(
        "legal_async_jobs",
        sa.Column("cancel_requested", sa.Integer(), nullable=False, server_default="0"),
    )


def _create_ws_event_logs() -> None:
    op.create_table(
        "ws_event_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(64), nullable=False, index=True),
        sa.Column("resume_token", sa.String(64), nullable=True, index=True),
        sa.Column("user_id", sa.Integer(), nullable=False, index=True),
        sa.Column("organization_id", sa.Integer(), nullable=True, index=True),
        sa.Column("channel", sa.String(32), nullable=True),
        sa.Column("seq_no", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("volatile", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.UniqueConstraint("session_id", "seq_no", name="uq_ws_event_logs_session_seq"),
    )


def upgrade() -> None:
    _extend_idempotency_keys()
    _extend_legal_async_jobs()
    _create_ws_event_logs()


def downgrade() -> None:
    op.drop_table("ws_event_logs")
    op.drop_column("legal_async_jobs", "cancel_requested")
    op.drop_index("ix_idempotency_keys_organization_id", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_user_id", table_name="idempotency_keys")
    op.drop_column("idempotency_keys", "resource_id")
    op.drop_column("idempotency_keys", "organization_id")
    op.drop_column("idempotency_keys", "user_id")
    op.drop_column("idempotency_keys", "endpoint")
