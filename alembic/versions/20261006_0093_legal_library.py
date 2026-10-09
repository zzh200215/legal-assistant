"""Persist legal library favorites and matter source links."""

import sqlalchemy as sa
from alembic import op

revision = "20261006_0093"
down_revision = "20261006_0092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "legal_source_favorites" not in tables:
        op.create_table(
            "legal_source_favorites",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_id", sa.Integer(), sa.ForeignKey("legal_sources.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("user_id", "source_id", name="uq_legal_source_favorite_user_source"),
        )
        op.create_index("ix_legal_source_favorites_user_id", "legal_source_favorites", ["user_id"])
        op.create_index("ix_legal_source_favorites_source_id", "legal_source_favorites", ["source_id"])
    if "legal_case_sources" not in tables:
        op.create_table(
            "legal_case_sources",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("case_id", sa.Integer(), sa.ForeignKey("legal_cases.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source_id", sa.Integer(), sa.ForeignKey("legal_sources.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("case_id", "source_id", name="uq_legal_case_source_case_source"),
        )
        op.create_index("ix_legal_case_sources_case_id", "legal_case_sources", ["case_id"])
        op.create_index("ix_legal_case_sources_source_id", "legal_case_sources", ["source_id"])
        op.create_index("ix_legal_case_sources_user_id", "legal_case_sources", ["user_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "legal_case_sources" in tables:
        for name in ("ix_legal_case_sources_user_id", "ix_legal_case_sources_source_id", "ix_legal_case_sources_case_id"):
            op.drop_index(name, table_name="legal_case_sources")
        op.drop_table("legal_case_sources")
    if "legal_source_favorites" in tables:
        for name in ("ix_legal_source_favorites_source_id", "ix_legal_source_favorites_user_id"):
            op.drop_index(name, table_name="legal_source_favorites")
        op.drop_table("legal_source_favorites")
