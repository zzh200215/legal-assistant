"""Add legal_consultations.trace_json to persist the consultation process trace.

copilot 的「处理过程时间线」此前只能由前端按响应字段事后回放（`deriveTraceReplay`），
因为咨询是一次性同步响应、服务端没有留下过程记录。这里给咨询记录加一列结构化 trace，
让面板能展示服务端真实记录的处理步骤（检索法源 / 生成建议 / 抽取事实 / 核验引用，
每步带状态、耗时与依据）。追问链路（`consultation_followup`）暂未埋点，其记录该列为空，
前端自动回退到事后回放。
"""

import sqlalchemy as sa

from alembic import op

revision = "20261018_0097"
down_revision = "20261010_0096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_consultations")}
    if "trace_json" not in columns:
        op.add_column("legal_consultations", sa.Column("trace_json", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_consultations")}
    if "trace_json" in columns:
        op.drop_column("legal_consultations", "trace_json")
