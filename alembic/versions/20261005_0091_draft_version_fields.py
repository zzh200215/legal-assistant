"""Store structured fields with legal draft version snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "20261005_0091"
down_revision = "20261005_0090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_document_versions")}
    if "fields_json" not in columns:
        op.add_column("legal_document_versions", sa.Column("fields_json", sa.Text(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("legal_document_versions")}
    if "fields_json" in columns:
        op.drop_column("legal_document_versions", "fields_json")
