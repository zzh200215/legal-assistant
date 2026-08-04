"""add org tenant and document sensitivity

Revision ID: 20260708_0012
Revises: 20260708_0011
Create Date: 2026-07-08 14:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260708_0012"
down_revision: Union[str, None] = "20260708_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_organizations_name", "organizations", ["name"])
    op.create_index("ix_organizations_code", "organizations", ["code"])

    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_departments_organization_id", "departments", ["organization_id"])
    op.create_index("ix_departments_name", "departments", ["name"])
    op.create_index("ix_departments_code", "departments", ["code"])

    op.add_column("users", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("department_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("job_title", sa.String(length=128), nullable=True))
    op.create_foreign_key("fk_users_organization_id", "users", "organizations", ["organization_id"], ["id"])
    op.create_foreign_key("fk_users_department_id", "users", "departments", ["department_id"], ["id"])
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_index("ix_users_department_id", "users", ["department_id"])

    op.add_column("knowledge_bases", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.add_column("knowledge_bases", sa.Column("department_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_knowledge_bases_organization_id", "knowledge_bases", "organizations", ["organization_id"], ["id"])
    op.create_foreign_key("fk_knowledge_bases_department_id", "knowledge_bases", "departments", ["department_id"], ["id"])
    op.create_index("ix_knowledge_bases_organization_id", "knowledge_bases", ["organization_id"])
    op.create_index("ix_knowledge_bases_department_id", "knowledge_bases", ["department_id"])

    op.add_column("documents", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("department_id", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("sensitivity_level", sa.String(length=32), nullable=False, server_default="internal"))
    op.create_foreign_key("fk_documents_organization_id", "documents", "organizations", ["organization_id"], ["id"])
    op.create_foreign_key("fk_documents_department_id", "documents", "departments", ["department_id"], ["id"])
    op.create_index("ix_documents_organization_id", "documents", ["organization_id"])
    op.create_index("ix_documents_department_id", "documents", ["department_id"])
    op.create_index("ix_documents_sensitivity_level", "documents", ["sensitivity_level"])


def downgrade() -> None:
    op.drop_index("ix_documents_sensitivity_level", table_name="documents")
    op.drop_index("ix_documents_department_id", table_name="documents")
    op.drop_index("ix_documents_organization_id", table_name="documents")
    op.drop_constraint("fk_documents_department_id", "documents", type_="foreignkey")
    op.drop_constraint("fk_documents_organization_id", "documents", type_="foreignkey")
    op.drop_column("documents", "sensitivity_level")
    op.drop_column("documents", "department_id")
    op.drop_column("documents", "organization_id")

    op.drop_index("ix_knowledge_bases_department_id", table_name="knowledge_bases")
    op.drop_index("ix_knowledge_bases_organization_id", table_name="knowledge_bases")
    op.drop_constraint("fk_knowledge_bases_department_id", "knowledge_bases", type_="foreignkey")
    op.drop_constraint("fk_knowledge_bases_organization_id", "knowledge_bases", type_="foreignkey")
    op.drop_column("knowledge_bases", "department_id")
    op.drop_column("knowledge_bases", "organization_id")

    op.drop_index("ix_users_department_id", table_name="users")
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_constraint("fk_users_department_id", "users", type_="foreignkey")
    op.drop_constraint("fk_users_organization_id", "users", type_="foreignkey")
    op.drop_column("users", "job_title")
    op.drop_column("users", "department_id")
    op.drop_column("users", "organization_id")

    op.drop_index("ix_departments_code", table_name="departments")
    op.drop_index("ix_departments_name", table_name="departments")
    op.drop_index("ix_departments_organization_id", table_name="departments")
    op.drop_table("departments")

    op.drop_index("ix_organizations_code", table_name="organizations")
    op.drop_index("ix_organizations_name", table_name="organizations")
    op.drop_table("organizations")
