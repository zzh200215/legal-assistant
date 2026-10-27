"""合同台账审查摘要：严重度细分计数（critical/medium/low）。

Revision ID: 20261027_0109
Revises: 20261027_0108

``risks_high`` 继续表示 high+critical（step36 口径），``risks_critical`` 是其子集；
``risks_medium`` / ``risks_low`` 按严重度计数，未知严重度归入 medium（与
``contract_risk_stats`` 一致）。回填只改三个新列，不动已有计数。
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "20261027_0109"
down_revision = "20261027_0108"
branch_labels = None
depends_on = None

TABLE = "legal_contract_review_summaries"
NEW_COLUMNS = ("risks_critical", "risks_medium", "risks_low")


def _table_exists() -> bool:
    return sa.inspect(op.get_bind()).has_table(TABLE)


def _existing_columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(TABLE)}


def _summaries():
    return sa.table(
        TABLE,
        sa.column("id", sa.Integer),
        sa.column("review_id", sa.Integer),
        sa.column("risks_critical", sa.Integer),
        sa.column("risks_medium", sa.Integer),
        sa.column("risks_low", sa.Integer),
    )


def _reviews():
    return sa.table(
        "legal_contract_reviews",
        sa.column("id", sa.Integer),
        sa.column("risks_json", sa.Text),
    )


def _counts(risks_json) -> dict[str, int]:
    try:
        risks = json.loads(risks_json or "[]")
    except (TypeError, ValueError):
        risks = []
    risks = [risk for risk in risks if isinstance(risk, dict)]
    counts = {"critical": 0, "medium": 0, "low": 0}
    for risk in risks:
        level = str(risk.get("risk_level") or "").strip().lower()
        if level == "critical":
            counts["critical"] += 1
        elif level == "low":
            counts["low"] += 1
        elif level != "high":
            counts["medium"] += 1
    return counts


def _backfill(bind) -> None:
    """按摘要行关联的审查重算三个新列；参数是 Connection，便于单测内存库。"""
    summaries = _summaries()
    reviews = _reviews()
    rows = bind.execute(
        sa.select(summaries.c.id, reviews.c.risks_json)
        .select_from(summaries.join(reviews, summaries.c.review_id == reviews.c.id))
    ).all()
    for summary_id, risks_json in rows:
        counts = _counts(risks_json)
        bind.execute(
            summaries.update()
            .where(summaries.c.id == summary_id)
            .values(**{f"risks_{key}": value for key, value in counts.items()})
        )


def upgrade() -> None:
    if not _table_exists():
        return
    columns = _existing_columns(op.get_bind())
    for name in NEW_COLUMNS:
        if name not in columns:
            op.add_column(
                TABLE,
                sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
            )
    _backfill(op.get_bind())


def downgrade() -> None:
    if not _table_exists():
        return
    columns = _existing_columns(op.get_bind())
    for name in reversed(NEW_COLUMNS):
        if name in columns:
            op.drop_column(TABLE, name)
