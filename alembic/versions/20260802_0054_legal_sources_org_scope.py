"""为 legal_sources 补齐 organization_id / scope 列（模型有、迁移漏写）。

LegalSource 模型声明了 organization_id（关联 organizations，NULL=个人法源库）与
scope（personal=个人 | team=团队共享），但迁移链从未为其加列，导致真实库上
全字段 SELECT（如 /api/legal/sources/{id}/articles 加载法源）报
Unknown column 'legal_sources.organization_id'。

存量行按「个人法源库」语义回填：organization_id 置 NULL，scope 置 'personal'。
scope 用 server_default 回填存量行并保持 NOT NULL；FK 约束与模型声明一致，
SQLite 方言不支持 ALTER 约束，仅 MySQL 建约束。

Revision ID: 20260802_0054
Revises: 20260802_0053
"""

from alembic import op
import sqlalchemy as sa

revision = "20260802_0054"
down_revision = "20260802_0053"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if not _table_exists(bind, "legal_sources"):
        return
    cols = {c["name"] for c in sa.inspect(bind).get_columns("legal_sources")}
    if "organization_id" not in cols:
        op.add_column(
            "legal_sources",
            sa.Column("organization_id", sa.Integer, nullable=True),
        )
        op.create_index("ix_legal_sources_organization_id", "legal_sources", ["organization_id"])
    if "scope" not in cols:
        op.add_column(
            "legal_sources",
            sa.Column("scope", sa.String(16), nullable=False, server_default="personal"),
        )
        op.create_index("ix_legal_sources_scope", "legal_sources", ["scope"])
    if not is_sqlite and "organization_id" not in cols:
        op.create_foreign_key(
            "fk_legal_sources_organization_id",
            "legal_sources",
            "organizations",
            ["organization_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if not _table_exists(bind, "legal_sources"):
        return
    cols = {c["name"] for c in sa.inspect(bind).get_columns("legal_sources")}
    if "organization_id" in cols:
        if not is_sqlite:
            op.drop_constraint("fk_legal_sources_organization_id", "legal_sources", type_="foreignkey")
        op.drop_index("ix_legal_sources_organization_id", table_name="legal_sources")
        op.drop_column("legal_sources", "organization_id")
    if "scope" in cols:
        op.drop_index("ix_legal_sources_scope", table_name="legal_sources")
        op.drop_column("legal_sources", "scope")
