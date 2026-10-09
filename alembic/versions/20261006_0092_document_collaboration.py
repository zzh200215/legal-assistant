"""Document collaboration notes and version labels."""

import sqlalchemy as sa
from alembic import op

revision = "20261006_0092"
down_revision = "20261005_0091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    version_columns = {column["name"] for column in inspector.get_columns("legal_document_versions")}
    if "version_note" not in version_columns:
        op.add_column("legal_document_versions", sa.Column("version_note", sa.String(length=512), nullable=True))
    tables = set(inspector.get_table_names())
    if "legal_document_comments" not in tables:
        op.create_table(
            "legal_document_comments",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("target_type", sa.String(length=32), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=True),
            sa.Column("line_start", sa.Integer(), nullable=True),
            sa.Column("line_end", sa.Integer(), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            # MySQL rejects DEFAULT values on TEXT columns. The application
            # model supplies [] for new comments, so keep this non-nullable
            # without a database-level default.
            sa.Column("mentions_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
            sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_legal_document_comments_target", "legal_document_comments", ["target_type", "target_id"])
        op.create_index("ix_legal_document_comments_version", "legal_document_comments", ["version"])
        op.create_index("ix_legal_document_comments_status", "legal_document_comments", ["status"])
        op.create_index("ix_legal_document_comments_author_id", "legal_document_comments", ["author_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "legal_document_comments" in set(inspector.get_table_names()):
        for name in (
            "ix_legal_document_comments_author_id",
            "ix_legal_document_comments_status",
            "ix_legal_document_comments_version",
            "ix_legal_document_comments_target",
        ):
            op.drop_index(name, table_name="legal_document_comments")
        op.drop_table("legal_document_comments")
    columns = {column["name"] for column in inspector.get_columns("legal_document_versions")}
    if "version_note" in columns:
        op.drop_column("legal_document_versions", "version_note")
