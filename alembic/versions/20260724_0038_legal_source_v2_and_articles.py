"""upgrade legal_source to V2, add legal_articles

Revision ID: 20260724_0038
Revises: 20260724_0037
"""

from alembic import op
import sqlalchemy as sa

revision = "20260724_0038"
down_revision = "20260724_0037"
branch_labels = None
depends_on = None


def upgrade():
    # ── LegalSource V2 新字段 ────────────────────────────────
    op.add_column("legal_sources", sa.Column("document_number", sa.String(64), nullable=True))
    op.add_column("legal_sources", sa.Column("promulgator", sa.String(128), nullable=True))
    op.add_column("legal_sources", sa.Column("promulgation_date", sa.Date(), nullable=True))
    op.add_column("legal_sources", sa.Column("full_text", sa.Text(), nullable=True))
    # MySQL 不允许 TEXT 列有默认值，设为 nullable，由 ORM 层处理默认
    op.add_column("legal_sources", sa.Column("law_area_json", sa.Text(), nullable=True))
    op.add_column("legal_sources", sa.Column("keywords_json", sa.Text(), nullable=True))
    op.add_column("legal_sources", sa.Column("amended_by_json", sa.Text(), nullable=True))
    op.add_column("legal_sources", sa.Column("amends_json", sa.Text(), nullable=True))

    # ── 法条表 ────────────────────────────────────────────────
    op.create_table(
        "legal_articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("legal_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("article_number", sa.String(32), nullable=False),
        sa.Column("title", sa.String(256)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chapter", sa.String(64)),
        sa.Column("section", sa.String(64)),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_articles_source_id", "legal_articles", ["source_id"])


def downgrade():
    op.drop_table("legal_articles")
    op.drop_column("legal_sources", "document_number")
    op.drop_column("legal_sources", "promulgator")
    op.drop_column("legal_sources", "promulgation_date")
    op.drop_column("legal_sources", "full_text")
    op.drop_column("legal_sources", "law_area_json")
    op.drop_column("legal_sources", "keywords_json")
    op.drop_column("legal_sources", "amended_by_json")
    op.drop_column("legal_sources", "amends_json")
