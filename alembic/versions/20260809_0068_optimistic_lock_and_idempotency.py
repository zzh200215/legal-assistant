"""乐观锁版本号列（version_id_col，旧数据回填）+ 通用幂等键台账表。

- 对文档/任务/案件/合同/组织加 version；审查结果/草稿已有业务 version，加独立 row_version。
  所有列为 NOT NULL + server_default=1，存量行自动回填为 1。
- 新增 idempotency_keys 表：scope+idempotency_key 唯一约束，作为并发幂等的最终保障。

Revision ID: 20260809_0068
Revises: 20260809_0067
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa

revision = "20260809_0068"
down_revision = "20260809_0067"
branch_labels = None
depends_on = None


def _add_version_column(table: str, column: str = "version") -> None:
    """加乐观锁列；server_default=1 回填存量行，NOT NULL 约束后续更新自动递增。"""
    op.add_column(
        table,
        sa.Column(column, sa.Integer(), nullable=False, server_default=sa.text("1")),
    )


def upgrade() -> None:
    # 乐观锁：无业务 version 的模型直接加 version
    _add_version_column("documents")
    _add_version_column("tasks")
    _add_version_column("legal_cases")
    _add_version_column("legal_contracts")
    _add_version_column("organizations")
    # 已有业务 version 的模型加独立的 row_version（避免与手动维护的版本号冲突）
    _add_version_column("legal_contract_reviews", "row_version")
    _add_version_column("legal_drafts", "row_version")

    # 通用幂等键台账
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("response_snapshot", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_keys_scope_key"),
    )
    op.create_index("ix_idempotency_keys_scope", "idempotency_keys", ["scope"])
    op.create_index("ix_idempotency_keys_status", "idempotency_keys", ["status"])
    op.create_index("ix_idempotency_keys_expires_at", "idempotency_keys", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_expires_at", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_status", table_name="idempotency_keys")
    op.drop_index("ix_idempotency_keys_scope", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")

    for table, column in (
        ("documents", "version"),
        ("tasks", "version"),
        ("legal_cases", "version"),
        ("legal_contracts", "version"),
        ("organizations", "version"),
        ("legal_contract_reviews", "row_version"),
        ("legal_drafts", "row_version"),
    ):
        op.drop_column(table, column)
