"""合同台账审查摘要物化表 + 存量回填。

Revision ID: 20261027_0108
Revises: 20261027_0107
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "20261027_0108"
down_revision = "20261027_0107"
branch_labels = None
depends_on = None

TABLE = "legal_contract_review_summaries"


def _has_table() -> bool:
    return sa.inspect(op.get_bind()).has_table(TABLE)


def _contracts():
    return sa.table("legal_contracts", sa.column("id", sa.Integer),
                    sa.column("organization_id", sa.Integer))


def _versions():
    return sa.table(
        "legal_contract_versions",
        sa.column("id", sa.Integer),
        sa.column("contract_id", sa.Integer),
        sa.column("version_no", sa.Integer),
        sa.column("source_review_id", sa.Integer),
    )


def _reviews():
    return sa.table(
        "legal_contract_reviews",
        sa.column("id", sa.Integer),
        sa.column("status", sa.String),
        sa.column("version", sa.Integer),
        sa.column("risks_json", sa.Text),
        sa.column("reviewed_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )


def _summaries():
    return sa.table(
        TABLE,
        sa.column("contract_id", sa.Integer),
        sa.column("organization_id", sa.Integer),
        sa.column("review_id", sa.Integer),
        sa.column("review_status", sa.String),
        sa.column("review_version", sa.Integer),
        sa.column("risks_total", sa.Integer),
        sa.column("risks_high", sa.Integer),
        sa.column("risks_unanchored", sa.Integer),
        sa.column("reviewed_at", sa.DateTime),
        sa.column("review_updated_at", sa.DateTime),
    )


def _counts(risks_json) -> tuple[int, int, int]:
    try:
        risks = json.loads(risks_json or "[]")
    except (TypeError, ValueError):
        risks = []
    risks = [risk for risk in risks if isinstance(risk, dict)]
    high = sum(1 for risk in risks if risk.get("risk_level") in ("high", "critical"))
    unanchored = sum(
        1 for risk in risks if not (risk.get("source_location") or {}).get("paragraph")
    )
    return len(risks), high, unanchored


def _backfill(bind) -> None:
    """按「版本号最大的带审查来源版本 → 审查」回填摘要行。

    参数是 Connection（而不是直接取 op.get_bind()），便于单测用内存库验证回填 SQL。
    """
    contracts = _contracts()
    versions = _versions()
    reviews = _reviews()
    summaries = _summaries()

    org_by_contract = {
        contract_id: org_id
        for contract_id, org_id in bind.execute(
            sa.select(contracts.c.id, contracts.c.organization_id)
        ).all()
    }
    latest: dict[int, int] = {}
    rows = bind.execute(
        sa.select(versions.c.contract_id, versions.c.source_review_id)
        .where(versions.c.source_review_id.isnot(None))
        .order_by(
            versions.c.contract_id.asc(),
            versions.c.version_no.desc(),
            versions.c.id.desc(),
        )
    ).all()
    for contract_id, review_id in rows:
        latest.setdefault(contract_id, review_id)

    if not latest:
        return
    review_rows = bind.execute(
        sa.select(
            reviews.c.id, reviews.c.status, reviews.c.version, reviews.c.risks_json,
            reviews.c.reviewed_at, reviews.c.updated_at,
        ).where(reviews.c.id.in_(set(latest.values())))
    ).all()
    review_by_id = {row[0]: row for row in review_rows}

    payload = []
    for contract_id, review_id in latest.items():
        review = review_by_id.get(review_id)
        if review is None or contract_id not in org_by_contract:
            continue
        total, high, unanchored = _counts(review[3])
        payload.append({
            "contract_id": contract_id,
            "organization_id": org_by_contract[contract_id],
            "review_id": review[0],
            "review_status": review[1] or "pending_review",
            "review_version": review[2],
            "risks_total": total,
            "risks_high": high,
            "risks_unanchored": unanchored,
            "reviewed_at": review[4],
            "review_updated_at": review[5],
        })
    if payload:
        bind.execute(summaries.insert(), payload)


def upgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("contract_id", sa.Integer(),
                  sa.ForeignKey("legal_contracts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Integer(),
                  sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("review_id", sa.Integer(),
                  sa.ForeignKey("legal_contract_reviews.id"), nullable=True),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=True),
        sa.Column("risks_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risks_high", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risks_unanchored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_legal_contract_review_summaries_contract_id", TABLE,
                    ["contract_id"], unique=True)
    op.create_index("ix_legal_contract_review_summaries_organization_id", TABLE,
                    ["organization_id"])
    op.create_index("ix_legal_contract_review_summaries_review_status", TABLE,
                    ["review_status"])
    _backfill(op.get_bind())


def downgrade() -> None:
    if _has_table():
        op.drop_index("ix_legal_contract_review_summaries_review_status", table_name=TABLE)
        op.drop_index("ix_legal_contract_review_summaries_organization_id", table_name=TABLE)
        op.drop_index("ix_legal_contract_review_summaries_contract_id", table_name=TABLE)
        op.drop_table(TABLE)
