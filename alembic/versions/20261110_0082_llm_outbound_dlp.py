"""P0 LLM 出站数据保护：llm_call_logs 增列（数据等级 / PII 统计 / 拦截原因 / provider）。

- provider：目标模型提供方（出站审计要求"目标模型/提供方"）。
- data_level：统一数据分级（public/internal/sensitive/highly_sensitive）。
- pii_hit_codes：命中规则 code 的 JSON 数组字符串（只存规则标识，绝不存原始 PII）。
- pii_hit_count / redacted_count：PII 命中与脱敏数量。
- blocked_reason：出站保护网关拦截原因（极敏感未放行 / 检测故障 fail closed 等）。
全部可空/有默认值，存量行零迁移成本。

Revision ID: 20261110_0082
Revises: 20261020_0081
Create Date: 2026-11-10
"""
from alembic import op
import sqlalchemy as sa

revision = "20261110_0082"
down_revision = "20261020_0081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("llm_call_logs") as batch_op:
        batch_op.add_column(sa.Column("provider", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("data_level", sa.String(32), nullable=True))
        batch_op.add_column(sa.Column("pii_hit_codes", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("pii_hit_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("redacted_count", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("blocked_reason", sa.String(128), nullable=True))
        batch_op.create_index("ix_llm_call_logs_data_level", ["data_level"])


def downgrade() -> None:
    with op.batch_alter_table("llm_call_logs") as batch_op:
        batch_op.drop_index("ix_llm_call_logs_data_level")
        batch_op.drop_column("blocked_reason")
        batch_op.drop_column("redacted_count")
        batch_op.drop_column("pii_hit_count")
        batch_op.drop_column("pii_hit_codes")
        batch_op.drop_column("data_level")
        batch_op.drop_column("provider")
