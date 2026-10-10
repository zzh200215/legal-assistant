"""Add users.onboarded_at for first-run onboarding guidance (ux-audit P1-2)."""

import sqlalchemy as sa
from alembic import op

revision = "20261010_0095"
down_revision = "20261009_0094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "users" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("users")}
        if "onboarded_at" not in columns:
            op.add_column("users", sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True))
            # 存量用户视为已完成引导：只有迁移之后新注册的用户才进入首次引导流程
            op.execute("UPDATE users SET onboarded_at = created_at WHERE onboarded_at IS NULL")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "users" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("users")}
        if "onboarded_at" in columns:
            op.drop_column("users", "onboarded_at")
