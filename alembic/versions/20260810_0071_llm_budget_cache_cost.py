"""LLM 预算桶 / 按 attempt 成本与估算列。

- token_usage 增加 budget_category（独立预算桶）、attempt_number（重试与最终成功区分）、
  cost（按定价计算的单次 attempt 成本）。
- llm_call_logs 增加 estimated_input_tokens / estimated_output_tokens（请求前统一估算）、
  attempt_number。

Revision ID: 20260810_0071
Revises: 20260810_0070
Create Date: 2026-08-10
"""
from alembic import op
import sqlalchemy as sa

revision = "20260810_0071"
down_revision = "20260810_0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("token_usage", sa.Column("budget_category", sa.String(32), nullable=True))
    op.add_column("token_usage", sa.Column("attempt_number", sa.Integer(), nullable=True))
    op.add_column("token_usage", sa.Column("cost", sa.Float(), nullable=True))
    op.create_index(
        "ix_token_usage_budget_category", "token_usage", ["budget_category"]
    )

    op.add_column("llm_call_logs", sa.Column("estimated_input_tokens", sa.Integer(), nullable=True))
    op.add_column("llm_call_logs", sa.Column("estimated_output_tokens", sa.Integer(), nullable=True))
    op.add_column("llm_call_logs", sa.Column("attempt_number", sa.Integer(), nullable=True))

    # 存量行按 action 回填预算桶（尽力而为；新行由运行时写入）。
    op.execute(
        sa.text(
            "UPDATE token_usage SET budget_category = CASE "
            "WHEN action = 'embedding' OR action LIKE 'embedding%' THEN 'embedding' "
            "WHEN action = 'generate_with_images' OR action LIKE 'vision%' THEN 'vision' "
            "WHEN action LIKE 'rerank%' THEN 'rerank' "
            "ELSE 'text' END "
            "WHERE budget_category IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("llm_call_logs", "attempt_number")
    op.drop_column("llm_call_logs", "estimated_output_tokens")
    op.drop_column("llm_call_logs", "estimated_input_tokens")
    op.drop_index("ix_token_usage_budget_category", table_name="token_usage")
    op.drop_column("token_usage", "cost")
    op.drop_column("token_usage", "attempt_number")
    op.drop_column("token_usage", "budget_category")
