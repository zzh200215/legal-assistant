"""add governed data analysis tables

Revision ID: 20260718_0031
Revises: 20260718_0030
"""
from alembic import op
import sqlalchemy as sa

revision = "20260718_0031"
down_revision = "20260718_0030"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("analysis_data_sources", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id")), sa.Column("name", sa.String(128), nullable=False), sa.Column("source_type", sa.String(32), nullable=False, server_default="internal_mysql"), sa.Column("status", sa.String(32), nullable=False, server_default="active"), sa.Column("allowed_tables_json", sa.Text(), nullable=False), sa.Column("schema_json", sa.Text()), sa.Column("semantic_json", sa.Text()), sa.Column("bi_config_json", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_analysis_data_sources_org", "analysis_data_sources", ["organization_id"])
    op.create_table("data_analysis_reports", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("source_id", sa.Integer(), sa.ForeignKey("analysis_data_sources.id"), nullable=False), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id")), sa.Column("question", sa.Text(), nullable=False), sa.Column("sql_text", sa.Text(), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="completed"), sa.Column("result_json", sa.Text(), nullable=False), sa.Column("chart_json", sa.Text()), sa.Column("summary", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_data_analysis_reports_source", "data_analysis_reports", ["source_id"])


def downgrade():
    op.drop_table("data_analysis_reports")
    op.drop_table("analysis_data_sources")
