"""合同台账 keyset 排序索引（step46）。

Revision ID: 20261027_0110
Revises: 20261027_0109

- 合同表：``(organization_id, updated_at, id)`` / ``(organization_id, created_at, id)``
  支持按合同时间排序 + 游标范围；``(organization_id, id)`` 支持空值尾段按 id 翻页。
- 摘要表：``(organization_id, reviewed_at, contract_id)`` /
  ``(organization_id, review_updated_at, contract_id)`` 让「按审查时间排序」由摘要表驱动，
  避免对 JOIN 结果 filesort。
"""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0110"
down_revision = "20261027_0109"
branch_labels = None
depends_on = None

INDEXES = (
    ("legal_contracts", "ix_legal_contracts_org_updated_at",
     ["organization_id", "updated_at", "id"]),
    ("legal_contracts", "ix_legal_contracts_org_created_at",
     ["organization_id", "created_at", "id"]),
    ("legal_contracts", "ix_legal_contracts_org_pk",
     ["organization_id", "id"]),
    ("legal_contract_review_summaries",
     "ix_legal_contract_review_summaries_org_reviewed",
     ["organization_id", "reviewed_at", "contract_id"]),
    ("legal_contract_review_summaries",
     "ix_legal_contract_review_summaries_org_review_updated",
     ["organization_id", "review_updated_at", "contract_id"]),
)


def _existing_indexes(bind, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return set()
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table, name, columns in INDEXES:
        if name in _existing_indexes(bind, table):
            continue
        op.create_index(name, table, columns)


def downgrade() -> None:
    bind = op.get_bind()
    for table, name, _columns in reversed(INDEXES):
        if name not in _existing_indexes(bind, table):
            continue
        op.drop_index(name, table_name=table)
