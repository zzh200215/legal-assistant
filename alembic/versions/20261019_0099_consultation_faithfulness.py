"""Add legal_consultations.faithfulness_json for post-generation faithfulness verdicts.

copilot 四层可解释性的最后一层（忠实度标记）此前常态显示「未校验」：判官只在离线评测里跑，
咨询链路从不调用它。这里加一列存「答案里的事实性断言是否都能在证据里找到依据」的判官结论。

与 trace/attribution 的关键差别是**时序**：这两者在生成时同步写入，忠实度是**事后补判**
（答案先返回，前端拿到结果后再触发一次判官调用），所以它单独一列、可单独刷新，
判官不可用时**不落库**（留空以便下次重试，而不是把「不可用」固化成结论）。

历史记录该列为空 → 前端按「未校验」渲染（不假装校验过）。
"""

import sqlalchemy as sa

from alembic import op

revision = "20261019_0099"
down_revision = "20261018_0098"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_consultations")}
    if "faithfulness_json" not in columns:
        op.add_column("legal_consultations", sa.Column("faithfulness_json", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_consultations")}
    if "faithfulness_json" in columns:
        op.drop_column("legal_consultations", "faithfulness_json")
