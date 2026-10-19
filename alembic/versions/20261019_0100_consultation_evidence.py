"""Add legal_consultations.evidence_json for the online evidence-verifiability summary.

三层评审的实测结论是：答复可信度的主变量不是「模型有多强」，而是**证据可得性**——
抽样里常常只有 1/8 条答复能看到法源条文正文，`verified=false` 的真实含义是「无法核验」
而不是「答错了」。这个事实此前只能离线算（`scripts/run_consultation_review.py`），
用户看不到。这里把同一口径在生成时算一次随响应返回：本次答复引用了多少条、
其中多少条真的核到了法源条文、多少条所在法源没有条文级数据、是否纳入了案件材料。

与 faithfulness_json 的区别：忠实度是判官**事后**补判（要额外模型调用），
本列是**生成时**的确定性统计，不花模型调用、不会失败，因此不落空、可长期留档。

历史记录该列为空 → 前端不渲染这个提示（不假装统计过）。
"""

import sqlalchemy as sa

from alembic import op

revision = "20261019_0100"
down_revision = "20261019_0099"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_consultations")}
    if "evidence_json" not in columns:
        op.add_column("legal_consultations", sa.Column("evidence_json", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_consultations")}
    if "evidence_json" in columns:
        op.drop_column("legal_consultations", "evidence_json")
