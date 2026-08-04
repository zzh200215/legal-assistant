"""#95/账号注销冷却期: users 加 deletion_requested_at / deletion_confirmed_at

SLA：注销 30 天冷却期可撤销；确认后主体字段匿名化（业务数据保留 5 年，user_id 约束 NOT NULL，
故采用字段抹除方案）。
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_0061"
down_revision = "20260908_0060"
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "users", "deletion_requested_at"):
        op.add_column("users", sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True))
    if not _column_exists(bind, "users", "deletion_confirmed_at"):
        op.add_column("users", sa.Column("deletion_confirmed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "users", "deletion_confirmed_at"):
        op.drop_column("users", "deletion_confirmed_at")
    if _column_exists(bind, "users", "deletion_requested_at"):
        op.drop_column("users", "deletion_requested_at")
