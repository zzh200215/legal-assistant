"""add document governance and agent approval

Revision ID: 20260707_0010
Revises: 20260626_0009
Create Date: 2026-07-07 15:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260707_0010"
down_revision: Union[str, None] = "20260626_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("permission_scope", sa.String(length=32), nullable=False, server_default="private"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_bases_user_id", "knowledge_bases", ["user_id"])
    op.create_index("ix_knowledge_bases_category", "knowledge_bases", ["category"])
    op.create_index("ix_knowledge_bases_permission_scope", "knowledge_bases", ["permission_scope"])

    op.add_column("documents", sa.Column("knowledge_base_id", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("parent_document_id", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("documents", sa.Column("content_hash", sa.String(length=128), nullable=True))
    op.add_column("documents", sa.Column("classification", sa.String(length=128), nullable=True))
    op.add_column("documents", sa.Column("tags", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("permission_scope", sa.String(length=32), nullable=False, server_default="private"))
    op.add_column("documents", sa.Column("permission_users", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("permission_roles", sa.Text(), nullable=True))
    op.add_column("documents", sa.Column("metadata_json", sa.Text(), nullable=True))
    op.create_foreign_key("fk_documents_knowledge_base_id", "documents", "knowledge_bases", ["knowledge_base_id"], ["id"])
    op.create_foreign_key("fk_documents_parent_document_id", "documents", "documents", ["parent_document_id"], ["id"])
    op.create_index("ix_documents_knowledge_base_id", "documents", ["knowledge_base_id"])
    op.create_index("ix_documents_parent_document_id", "documents", ["parent_document_id"])
    op.create_index("ix_documents_content_hash", "documents", ["content_hash"])
    op.create_index("ix_documents_classification", "documents", ["classification"])
    op.create_index("ix_documents_permission_scope", "documents", ["permission_scope"])

    op.create_table(
        "document_access_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_value", sa.String(length=128), nullable=False),
        sa.Column("permission", sa.String(length=32), nullable=False, server_default="read"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_access_rules_document_id", "document_access_rules", ["document_id"])
    op.create_index("ix_document_access_rules_subject_type", "document_access_rules", ["subject_type"])
    op.create_index("ix_document_access_rules_subject_value", "document_access_rules", ["subject_value"])

    op.create_table(
        "agent_approval_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("agent_type", sa.String(length=64), nullable=True),
        sa.Column("input_params", sa.Text(), nullable=True),
        sa.Column("risk_level", sa.String(length=32), nullable=False, server_default="high"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("approval_token", sa.String(length=128), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_token"),
    )
    op.create_index("ix_agent_approval_requests_agent_run_id", "agent_approval_requests", ["agent_run_id"])
    op.create_index("ix_agent_approval_requests_user_id", "agent_approval_requests", ["user_id"])
    op.create_index("ix_agent_approval_requests_tool_name", "agent_approval_requests", ["tool_name"])
    op.create_index("ix_agent_approval_requests_agent_type", "agent_approval_requests", ["agent_type"])
    op.create_index("ix_agent_approval_requests_status", "agent_approval_requests", ["status"])
    op.create_index("ix_agent_approval_requests_approval_token", "agent_approval_requests", ["approval_token"])


def downgrade() -> None:
    op.drop_index("ix_agent_approval_requests_approval_token", table_name="agent_approval_requests")
    op.drop_index("ix_agent_approval_requests_status", table_name="agent_approval_requests")
    op.drop_index("ix_agent_approval_requests_agent_type", table_name="agent_approval_requests")
    op.drop_index("ix_agent_approval_requests_tool_name", table_name="agent_approval_requests")
    op.drop_index("ix_agent_approval_requests_user_id", table_name="agent_approval_requests")
    op.drop_index("ix_agent_approval_requests_agent_run_id", table_name="agent_approval_requests")
    op.drop_table("agent_approval_requests")

    op.drop_index("ix_document_access_rules_subject_value", table_name="document_access_rules")
    op.drop_index("ix_document_access_rules_subject_type", table_name="document_access_rules")
    op.drop_index("ix_document_access_rules_document_id", table_name="document_access_rules")
    op.drop_table("document_access_rules")

    op.drop_index("ix_documents_permission_scope", table_name="documents")
    op.drop_index("ix_documents_classification", table_name="documents")
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_index("ix_documents_parent_document_id", table_name="documents")
    op.drop_index("ix_documents_knowledge_base_id", table_name="documents")
    op.drop_constraint("fk_documents_parent_document_id", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_knowledge_base_id", "documents", type_="foreignkey")
    op.drop_column("documents", "metadata_json")
    op.drop_column("documents", "permission_roles")
    op.drop_column("documents", "permission_users")
    op.drop_column("documents", "permission_scope")
    op.drop_column("documents", "tags")
    op.drop_column("documents", "classification")
    op.drop_column("documents", "content_hash")
    op.drop_column("documents", "version_number")
    op.drop_column("documents", "parent_document_id")
    op.drop_column("documents", "knowledge_base_id")

    op.drop_index("ix_knowledge_bases_permission_scope", table_name="knowledge_bases")
    op.drop_index("ix_knowledge_bases_category", table_name="knowledge_bases")
    op.drop_index("ix_knowledge_bases_user_id", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
