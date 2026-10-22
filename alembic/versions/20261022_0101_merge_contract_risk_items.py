"""合并合同审查风险存储：contract_risk_items → legal_contract_reviews.risks_json。

背景：同一批风险同时存在两张表——`risks_json`（模型原始输出 + 证据链/门禁/锚点读它）与
`contract_risk_items`（结构化风险项 + 处置状态 + claims/evidence 的上游）。两份会漂移，
处置状态写在表里、缺口统计读 JSON，互不知情。

本迁移把风险项上的处置信息回填进 `risks_json`（按 review 内 id 升序 = JSON 数组下标，
因为风险项就是按 risks 顺序创建的），然后删表；`legal_claims.risk_item_id`
（无 FK 的多态列）一并删除——claims 通过 `source_type + source_id` 归属审查记录。

Revision ID: 20261022_0101
Revises: 20261019_0100
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "20261022_0101"
down_revision = "20261019_0100"
branch_labels = None
depends_on = None

TABLE = "contract_risk_items"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_column(table: str, column: str) -> bool:
    return _has_table(table) and column in {item["name"] for item in _inspector().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    return _has_table(table) and any(item.get("name") == name for item in _inspector().get_indexes(table))


def _loads(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _backfill_risk_status(bind) -> None:
    """把风险项的处置状态/证据回填到对应 reviews.risks_json 的元素上（幂等）。"""
    if not (_has_table(TABLE) and _has_table("legal_contract_reviews")):
        return
    rows = bind.execute(sa.text(
        f"SELECT id, review_id, status, review_status, reviewer_id, resolved_at,"
        f" resolution_note, evidence_json FROM {TABLE} ORDER BY review_id, id"
    )).mappings().all()
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(int(row["review_id"]), []).append(row)

    for review_id, items in grouped.items():
        review = bind.execute(
            sa.text("SELECT risks_json FROM legal_contract_reviews WHERE id = :id"),
            {"id": review_id},
        ).mappings().first()
        if review is None:
            continue
        risks = _loads(review["risks_json"])
        changed = False
        for index, item in enumerate(items):
            if index >= len(risks) or not isinstance(risks[index], dict):
                continue
            risk = risks[index]
            status = item.get("status")
            if status and risk.get("status") != status:
                risk["status"] = status
                changed = True
            for key in ("reviewer_id", "resolution_note"):
                value = item.get(key)
                if value is not None and risk.get(key) != value:
                    risk[key] = value
                    changed = True
            resolved_at = item.get("resolved_at")
            if resolved_at is not None:
                serialized = resolved_at.isoformat() if hasattr(resolved_at, "isoformat") else str(resolved_at)
                if risk.get("resolved_at") != serialized:
                    risk["resolved_at"] = serialized
                    changed = True
            evidence = _loads(item.get("evidence_json"))
            if isinstance(evidence, list):
                evidence = {}
            if isinstance(evidence, dict) and evidence and not risk.get("source_location"):
                risk["source_location"] = evidence
                changed = True
        if changed:
            bind.execute(
                sa.text("UPDATE legal_contract_reviews SET risks_json = :risks WHERE id = :id"),
                {"risks": json.dumps(risks, ensure_ascii=False), "id": review_id},
            )


def upgrade() -> None:
    bind = op.get_bind()
    _backfill_risk_status(bind)

    if _has_table(TABLE):
        op.drop_table(TABLE)

    if _has_column("legal_claims", "risk_item_id"):
        if _has_index("legal_claims", "ix_legal_claims_risk_item_id"):
            op.drop_index("ix_legal_claims_risk_item_id", table_name="legal_claims")
        with op.batch_alter_table("legal_claims") as batch:
            batch.drop_column("risk_item_id")


def downgrade() -> None:
    """恢复表与列（结构可回滚，数据不可回滚：风险项需由 risks_json 重新派生）。"""
    if not _has_column("legal_claims", "risk_item_id"):
        with op.batch_alter_table("legal_claims") as batch:
            batch.add_column(sa.Column("risk_item_id", sa.Integer(), nullable=True))
        op.create_index("ix_legal_claims_risk_item_id", "legal_claims", ["risk_item_id"])

    if not _has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), nullable=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("case_id", sa.Integer(), nullable=True),
            sa.Column("contract_id", sa.Integer(), nullable=True),
            sa.Column("contract_version_id", sa.Integer(), nullable=True),
            sa.Column("review_id", sa.Integer(), nullable=False),
            sa.Column("clause_id", sa.Integer(), nullable=True),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="medium"),
            sa.Column("title", sa.String(length=256), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("evidence_json", sa.Text(), nullable=True),
            sa.Column("recommendation", sa.Text(), nullable=True),
            sa.Column("suggested_revision", sa.Text(), nullable=True),
            sa.Column("original_text_excerpt", sa.String(length=1000), nullable=True),
            sa.Column("legal_basis_json", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=16), nullable=False, server_default="model"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="open"),
            sa.Column("review_status", sa.String(length=24), nullable=True),
            sa.Column("reviewer_id", sa.Integer(), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolution_note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_contract_risk_items_review_id", TABLE, ["review_id"])
        op.create_index("ix_contract_risk_items_case_id", TABLE, ["case_id"])
        op.create_index("ix_contract_risk_items_severity", TABLE, ["severity"])
        op.create_index("ix_contract_risk_items_status", TABLE, ["status"])
