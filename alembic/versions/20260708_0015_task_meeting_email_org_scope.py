"""add org scope to task meeting email

Revision ID: 20260708_0015
Revises: 20260708_0014
Create Date: 2026-07-09 00:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260708_0015"
down_revision = "20260708_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("department_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_tasks_organization_id"), "tasks", ["organization_id"], unique=False)
    op.create_index(op.f("ix_tasks_department_id"), "tasks", ["department_id"], unique=False)
    op.create_foreign_key(None, "tasks", "organizations", ["organization_id"], ["id"])
    op.create_foreign_key(None, "tasks", "departments", ["department_id"], ["id"])

    op.add_column("meetings", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.add_column("meetings", sa.Column("department_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_meetings_organization_id"), "meetings", ["organization_id"], unique=False)
    op.create_index(op.f("ix_meetings_department_id"), "meetings", ["department_id"], unique=False)
    op.create_foreign_key(None, "meetings", "organizations", ["organization_id"], ["id"])
    op.create_foreign_key(None, "meetings", "departments", ["department_id"], ["id"])

    op.add_column("email_drafts", sa.Column("organization_id", sa.Integer(), nullable=True))
    op.add_column("email_drafts", sa.Column("department_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_email_drafts_organization_id"), "email_drafts", ["organization_id"], unique=False)
    op.create_index(op.f("ix_email_drafts_department_id"), "email_drafts", ["department_id"], unique=False)
    op.create_foreign_key(None, "email_drafts", "organizations", ["organization_id"], ["id"])
    op.create_foreign_key(None, "email_drafts", "departments", ["department_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(None, "email_drafts", type_="foreignkey")
    op.drop_constraint(None, "email_drafts", type_="foreignkey")
    op.drop_index(op.f("ix_email_drafts_department_id"), table_name="email_drafts")
    op.drop_index(op.f("ix_email_drafts_organization_id"), table_name="email_drafts")
    op.drop_column("email_drafts", "department_id")
    op.drop_column("email_drafts", "organization_id")

    op.drop_constraint(None, "meetings", type_="foreignkey")
    op.drop_constraint(None, "meetings", type_="foreignkey")
    op.drop_index(op.f("ix_meetings_department_id"), table_name="meetings")
    op.drop_index(op.f("ix_meetings_organization_id"), table_name="meetings")
    op.drop_column("meetings", "department_id")
    op.drop_column("meetings", "organization_id")

    op.drop_constraint(None, "tasks", type_="foreignkey")
    op.drop_constraint(None, "tasks", type_="foreignkey")
    op.drop_index(op.f("ix_tasks_department_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_organization_id"), table_name="tasks")
    op.drop_column("tasks", "department_id")
    op.drop_column("tasks", "organization_id")
