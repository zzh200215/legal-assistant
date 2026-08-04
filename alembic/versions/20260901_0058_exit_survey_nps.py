"""#72/试点退出问卷与 NPS 回收: 建 exit_surveys / nps_responses 表

对应 docs/pilot-success-playbook.md §5 退出问卷（A. NPS 0-10；B. 信任三件套；
C. 业务价值；D. 付费意愿；E. 开放反馈）与轻量 NPS 打分。
"""
from alembic import op
import sqlalchemy as sa

revision = "20260901_0058"
down_revision = "20260804_0057"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "exit_surveys"):
        op.create_table(
            "exit_surveys",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True, index=True),
            sa.Column("nps_score", sa.Integer(), nullable=True, comment="A. 0-10 推荐分"),
            sa.Column("trust_confidence", sa.String(16), nullable=True, comment="B1 credible/indifferent/not_trusted"),
            sa.Column("trust_citations", sa.String(16), nullable=True, comment="B2 frequent/occasional/never"),
            sa.Column("trust_next_steps", sa.String(16), nullable=True, comment="B3 clear/indifferent/missing"),
            sa.Column("value_ranking", sa.String(64), nullable=True, comment="C4 consult>review>draft"),
            sa.Column("review_wish", sa.Text(), nullable=True, comment="C5 提交审核后最想要什么"),
            sa.Column("pain_point", sa.Text(), nullable=True, comment="C6 最卡/最想砍掉的环节"),
            sa.Column("pay_intent", sa.String(16), nullable=True, comment="D7 renew/try_more/expensive/wont"),
            sa.Column("feature_requests", sa.Text(), nullable=True, comment="E8 最希望加的 1-2 个功能"),
            sa.Column("summary_feedback", sa.Text(), nullable=True, comment="E9 一句话总结"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        )
    if not _table_exists(bind, "nps_responses"):
        op.create_table(
            "nps_responses",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True, index=True),
            sa.Column("score", sa.Integer(), nullable=False, comment="0-10"),
            sa.Column("source", sa.String(16), nullable=False, server_default="in_app", comment="in_app/exit_survey"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "exit_surveys"):
        op.drop_table("exit_surveys")
    if _table_exists(bind, "nps_responses"):
        op.drop_table("nps_responses")
