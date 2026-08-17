"""P1-C Webhook 统一验签：webhook_nonces 表（跨实例 nonce 去重）。

- UNIQUE(namespace, nonce)：并发重放同一 nonce 仅一个成功（共享存储去重）。
- expires_at 按 WEBHOOK_REPLAY_TTL_SECONDS 写入，过期行由写入路径惰性清理。
- 只存 nonce 与命名空间，不存密钥或载荷。

Revision ID: 20261201_0083
Revises: 20261110_0082
Create Date: 2026-12-01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261201_0083"
down_revision = "20261110_0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webhook_nonces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("namespace", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("namespace", "nonce", name="uq_webhook_nonces_namespace_nonce"),
    )
    op.create_index("ix_webhook_nonces_namespace", "webhook_nonces", ["namespace"])
    op.create_index("ix_webhook_nonces_expires_at", "webhook_nonces", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_webhook_nonces_expires_at", table_name="webhook_nonces")
    op.drop_index("ix_webhook_nonces_namespace", table_name="webhook_nonces")
    op.drop_table("webhook_nonces")