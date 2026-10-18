"""Add legal_consultations.attribution_json to persist sentence-level citation mapping.

copilot 的「逐句溯源」此前只能退化到整篇答案粒度：响应里只有 references（本次引用了什么），
没有「哪一句用了哪条」。这里加一列句级映射，写入每句建议对应的引用下标、词面重合度与共同词，
让面板能按句高亮真正支撑它的证据；匹配不上的句子如实留空（不把整篇引用塞给每一句）。
追问链路（`consultation_followup`）与历史记录该列为空，前端自动回退到整篇答案粒度。
"""

import sqlalchemy as sa

from alembic import op

revision = "20261018_0098"
down_revision = "20261018_0097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_consultations")}
    if "attribution_json" not in columns:
        op.add_column("legal_consultations", sa.Column("attribution_json", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_consultations")}
    if "attribution_json" in columns:
        op.drop_column("legal_consultations", "attribution_json")
