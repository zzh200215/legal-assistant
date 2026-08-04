"""add connector department scope

Revision ID: 20260708_0016
Revises: 20260708_0015
Create Date: 2026-07-08 23:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260708_0016"
down_revision: Union[str, None] = "20260708_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("external_connectors", sa.Column("department_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_external_connectors_department_id_departments",
        "external_connectors",
        "departments",
        ["department_id"],
        ["id"],
    )
    op.create_index("ix_external_connectors_department_id", "external_connectors", ["department_id"])


def downgrade() -> None:
    op.drop_index("ix_external_connectors_department_id", table_name="external_connectors")
    op.drop_constraint("fk_external_connectors_department_id_departments", "external_connectors", type_="foreignkey")
    op.drop_column("external_connectors", "department_id")
