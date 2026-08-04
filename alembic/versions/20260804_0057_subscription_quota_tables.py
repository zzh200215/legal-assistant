"""#40/配额体系: 补建 subscription_plans / user_subscriptions / quota_usages 三表。

生产库此前从未有订阅/配额表（计费走 legal_billing 组织维度），但配额检查
（subscription_service.check_quota / ensure_default_plans）依赖这三张表，
导致试点主路径（咨询/审查/文书 POST）在生产库 500。对照模型补齐。
"""
from alembic import op
import sqlalchemy as sa

revision = "20260804_0057"
down_revision = "20260804_0056"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "subscription_plans"):
        op.create_table(
            "subscription_plans",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tier", sa.String(32), nullable=False, unique=True, index=True),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("price_monthly", sa.Numeric(10, 2), nullable=False, server_default="0"),
            sa.Column("quota_consultation", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("quota_review", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("quota_draft", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    if not _table_exists(bind, "user_subscriptions"):
        op.create_table(
            "user_subscriptions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("plan_id", sa.Integer(), sa.ForeignKey("subscription_plans.id"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="active", index=True),
            sa.Column("payment_provider", sa.String(32), nullable=True),
            sa.Column("payment_subscription_id", sa.String(128), nullable=True, index=True),
            sa.Column("payment_customer_id", sa.String(128), nullable=True, index=True),
            sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    if not _table_exists(bind, "quota_usages"):
        op.create_table(
            "quota_usages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("year_month", sa.String(7), nullable=False, index=True),
            sa.Column("consultation_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("draft_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("user_id", "year_month", name="uq_quota_usage_user_month"),
        )


def downgrade() -> None:
    op.drop_table("quota_usages")
    op.drop_table("user_subscriptions")
    op.drop_table("subscription_plans")
