"""合同台账搜索：title/counterparty/contract_no 的 ngram 全文索引（step47）。

Revision ID: 20261027_0111
Revises: 20261027_0110

MySQL 8 的 InnoDB FULLTEXT + ngram 解析器支持中文子串检索，用来替代
``title LIKE '%关键词%'`` 的全表扫描。ngram_token_size 默认 2，因此单字查询仍回退 LIKE。
非 MySQL 方言（SQLite 开发/测试）不建全文索引，查询侧也回退 LIKE。
"""

import sqlalchemy as sa

from alembic import op

revision = "20261027_0111"
down_revision = "20261027_0110"
branch_labels = None
depends_on = None

INDEX_NAME = "ft_legal_contracts_search"
TABLE = "legal_contracts"
COLUMNS = "title, counterparty, contract_no"


def _index_exists(bind) -> bool:
    return bool(bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :table "
            "AND index_name = :index"
        ),
        {"table": TABLE, "index": INDEX_NAME},
    ).scalar())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    if _index_exists(bind):
        return
    op.execute(
        f"ALTER TABLE {TABLE} ADD FULLTEXT INDEX {INDEX_NAME} ({COLUMNS}) WITH PARSER ngram"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    if not _index_exists(bind):
        return
    op.execute(f"ALTER TABLE {TABLE} DROP INDEX {INDEX_NAME}")
