"""为咨询/审查/文书三张业务表补齐 case_id 列（模型有、迁移漏写）。

legal_consultations / legal_contract_reviews / legal_drafts 在模型里声明了
case_id（关联 legal_cases，Phase 12「案件绑定」），但迁移链从未为其加列，
导致真实库上任何全字段 SELECT（如 /api/admin/dashboard 的 count）报
Unknown column 'case_id'。补加可空列 + 索引，存量数据无需回填。
FK 约束与模型声明一致；SQLite 方言不支持 ALTER 约束，仅 MySQL 建约束。

Revision ID: 20260802_0053
Revises: 20260802_0052
"""

from alembic import op
import sqlalchemy as sa

revision = "20260802_0053"
down_revision = "20260802_0052"
branch_labels = None
depends_on = None

TABLES = [
    ("legal_consultations", "ix_legal_consultations_case_id", "fk_legal_consultations_case_id"),
    ("legal_contract_reviews", "ix_legal_contract_reviews_case_id", "fk_legal_contract_reviews_case_id"),
    ("legal_drafts", "ix_legal_drafts_case_id", "fk_legal_drafts_case_id"),
]


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if not _table_exists(bind, "legal_cases"):
        # 没有 legal_cases 表的库（如纯模型 create_all 的测试环境）跳过
        return
    for table, index_name, fk_name in TABLES:
        if not _table_exists(bind, table):
            continue
        cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if "case_id" in cols:
            continue
        op.add_column(table, sa.Column("case_id", sa.Integer, nullable=True))
        op.create_index(index_name, table, ["case_id"])
        if not is_sqlite:
            op.create_foreign_key(fk_name, table, "legal_cases", ["case_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    for table, index_name, fk_name in TABLES:
        if not _table_exists(bind, table):
            continue
        cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if "case_id" not in cols:
            continue
        if not is_sqlite:
            op.drop_constraint(fk_name, table, type_="foreignkey")
        op.drop_index(index_name, table_name=table)
        op.drop_column(table, "case_id")
