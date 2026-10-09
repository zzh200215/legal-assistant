"""Add organizations.org_type to distinguish personal sandbox orgs from team orgs."""

import sqlalchemy as sa
from alembic import op

revision = "20261009_0094"
down_revision = "20261006_0093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "organizations" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("organizations")}
        if "org_type" not in columns:
            op.add_column(
                "organizations",
                sa.Column("org_type", sa.String(16), nullable=False, server_default="team"),
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "organizations" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("organizations")}
        if "org_type" in columns:
            op.drop_column("organizations", "org_type")
