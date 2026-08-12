"""P1 法律业务统一模型：结构化表 + 法源适用性字段 + 审核版本绑定。

- 新表：legal_facts / legal_evidences / legal_claims / legal_references / contract_risk_items，
  承载 Fact / Evidence / Claim(legal_conclusion|risk_warning|fact_to_confirm) / Reference /
  ContractRiskItem，实现 Claim -> Evidence -> Reference 数据契约。
- 扩展：legal_sources 加 expiration_date / applicability_scope / canonical_identifier；
  legal_review_actions 加 target_version（审核绑定版本）；
  legal_consultations / legal_contract_reviews / legal_drafts 加 model_snapshot_json / reviewed_version。
- 回填：把既有 JSON 列（risks_json / references_json / known_facts_json / missing_facts_json）
  幂等映射到新表，旧数据可查询、可审核。

Revision ID: 20260812_0074
Revises: 20260813_0073
Create Date: 2026-08-12
"""
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

revision = "20260812_0074"
down_revision = "20260813_0073"
branch_labels = None
depends_on = None

SEVERITIES = ("low", "medium", "high", "critical")


def _severe(severity: str) -> bool:
    return severity in ("high", "critical")


# ── 建表 ────────────────────────────────────────────────────────────────────

def _create_legal_facts() -> None:
    op.create_table(
        "legal_facts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("consultation_id", sa.Integer(), nullable=True),
        sa.Column("fact_type", sa.String(16), nullable=False, server_default="known"),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="model"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["legal_cases.id"]),
        sa.ForeignKeyConstraint(["consultation_id"], ["legal_consultations.id"]),
    )
    op.create_index("ix_legal_facts_user_id", "legal_facts", ["user_id"])
    op.create_index("ix_legal_facts_case_id", "legal_facts", ["case_id"])
    op.create_index("ix_legal_facts_consultation_id", "legal_facts", ["consultation_id"])
    op.create_index("ix_legal_facts_fact_type", "legal_facts", ["fact_type"])


def _create_legal_claims() -> None:
    op.create_table(
        "legal_claims",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("risk_item_id", sa.Integer(), nullable=True),
        sa.Column("claim_type", sa.String(24), nullable=False),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("assumptions_json", sa.Text(), nullable=True),
        sa.Column("limitations_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("review_status", sa.String(24), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="model"),
        sa.Column("model_snapshot_json", sa.Text(), nullable=True),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["legal_cases.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
    )
    op.create_index("ix_legal_claims_user_id", "legal_claims", ["user_id"])
    op.create_index("ix_legal_claims_case_id", "legal_claims", ["case_id"])
    op.create_index("ix_legal_claims_source", "legal_claims", ["source_type", "source_id"])
    op.create_index("ix_legal_claims_claim_type", "legal_claims", ["claim_type"])
    op.create_index("ix_legal_claims_status", "legal_claims", ["status"])
    op.create_index("ix_legal_claims_risk_item_id", "legal_claims", ["risk_item_id"])


def _create_legal_evidences() -> None:
    op.create_table(
        "legal_evidences",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("claim_id", sa.Integer(), nullable=True),
        sa.Column("fact_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False, server_default="support"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("extraction_method", sa.String(64), nullable=True),
        sa.Column("source_type", sa.String(24), nullable=False, server_default="document"),
        sa.Column("document_id", sa.Integer(), nullable=True),
        sa.Column("contract_clause_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("loc_json", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("excerpt", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["legal_cases.id"]),
        sa.ForeignKeyConstraint(["claim_id"], ["legal_claims.id"]),
        sa.ForeignKeyConstraint(["fact_id"], ["legal_facts.id"]),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["contract_clause_id"], ["legal_contract_clauses.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["legal_sources.id"]),
        sa.ForeignKeyConstraint(["article_id"], ["legal_articles.id"]),
    )
    op.create_index("ix_legal_evidences_user_id", "legal_evidences", ["user_id"])
    op.create_index("ix_legal_evidences_case_id", "legal_evidences", ["case_id"])
    op.create_index("ix_legal_evidences_claim_id", "legal_evidences", ["claim_id"])
    op.create_index("ix_legal_evidences_fact_id", "legal_evidences", ["fact_id"])


def _create_legal_references() -> None:
    op.create_table(
        "legal_references",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("claim_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=True),
        sa.Column("article_number", sa.String(32), nullable=True),
        sa.Column("chapter", sa.String(64), nullable=True),
        sa.Column("section", sa.String(64), nullable=True),
        sa.Column("paragraph", sa.Integer(), nullable=True),
        sa.Column("citation_text", sa.String(512), nullable=True),
        sa.Column("analysis_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("jurisdiction", sa.String(128), nullable=True),
        sa.Column("applicable", sa.Integer(), nullable=True),
        sa.Column("applicability_note", sa.String(512), nullable=True),
        sa.Column("source_version_snapshot", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["legal_cases.id"]),
        sa.ForeignKeyConstraint(["claim_id"], ["legal_claims.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["legal_sources.id"]),
        sa.ForeignKeyConstraint(["article_id"], ["legal_articles.id"]),
    )
    op.create_index("ix_legal_references_user_id", "legal_references", ["user_id"])
    op.create_index("ix_legal_references_case_id", "legal_references", ["case_id"])
    op.create_index("ix_legal_references_claim_id", "legal_references", ["claim_id"])
    op.create_index("ix_legal_references_source_id", "legal_references", ["source_id"])


def _create_contract_risk_items() -> None:
    op.create_table(
        "contract_risk_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("contract_id", sa.Integer(), nullable=True),
        sa.Column("contract_version_id", sa.Integer(), nullable=True),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("clause_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("title", sa.String(256), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("recommendation", sa.Text(), nullable=True),
        sa.Column("suggested_revision", sa.Text(), nullable=True),
        sa.Column("original_text_excerpt", sa.String(1000), nullable=True),
        sa.Column("legal_basis_json", sa.Text(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="model"),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column("review_status", sa.String(24), nullable=True),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["legal_cases.id"]),
        sa.ForeignKeyConstraint(["contract_id"], ["legal_contracts.id"]),
        sa.ForeignKeyConstraint(["contract_version_id"], ["legal_contract_versions.id"]),
        sa.ForeignKeyConstraint(["review_id"], ["legal_contract_reviews.id"]),
        sa.ForeignKeyConstraint(["clause_id"], ["legal_contract_clauses.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
    )
    op.create_index("ix_contract_risk_items_user_id", "contract_risk_items", ["user_id"])
    op.create_index("ix_contract_risk_items_case_id", "contract_risk_items", ["case_id"])
    op.create_index("ix_contract_risk_items_review_id", "contract_risk_items", ["review_id"])
    op.create_index("ix_contract_risk_items_severity", "contract_risk_items", ["severity"])
    op.create_index("ix_contract_risk_items_status", "contract_risk_items", ["status"])


# ── 回填（幂等）─────────────────────────────────────────────────────────────

def _json_list(raw: str | None):
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _ref_to_reference(session: Session, ref: dict, *, org_id, user_id, case_id, claim_id=None) -> None:
    if not isinstance(ref, dict) or not ref.get("source_id"):
        return
    from app.models.legal_domain import LegalReference
    snapshot = {
        k: ref.get(k) for k in ("version", "status", "effective_date")
        if ref.get(k) is not None
    }
    session.add(LegalReference(
        organization_id=org_id,
        user_id=user_id,
        case_id=case_id,
        claim_id=claim_id,
        source_id=ref["source_id"],
        article_number=ref.get("article_number"),
        citation_text=(ref.get("citation") or ref.get("title") or "")[:512] or None,
        jurisdiction=ref.get("jurisdiction"),
        applicable=None,
        applicability_note="历史数据回填：适用性待核验",
        source_version_snapshot=json.dumps(snapshot, ensure_ascii=False)[:512] if snapshot else None,
    ))


def _backfill(bind) -> None:
    """把既有 JSON 列幂等映射到结构化新表。

    幂等策略：
    - risk_items：按 review_id 判断是否已回填；
    - facts：按 consultation_id 判断是否已回填；
    - references：无多态回指列，整体判断（表为空才回填）。迁移只执行一次，
      部分失败后重跑最多造成部分遗漏，旧数据仍可经 JSON 列读取。
    """
    from app.models.legal import ContractReview, LegalConsultation, LegalDraft, LegalCase
    from app.models.legal_domain import ContractRiskItem, LegalFact, LegalReference

    session = Session(bind=bind)
    try:
        # case -> organization_id 映射（consultation/review/draft 无 org 列，需从 case 派生）
        case_org = {c.id: c.organization_id for c in session.query(LegalCase).all()}
        refs_backfilled = session.query(LegalReference).count() > 0

        # 1) contract_risk_items + legal_references from contract reviews
        for review in session.query(ContractReview).all():
            org_id = case_org.get(review.case_id) if review.case_id else None
            if session.query(ContractRiskItem).filter(ContractRiskItem.review_id == review.id).count() == 0:
                for item in _json_list(review.risks_json):
                    if not isinstance(item, dict):
                        continue
                    severity = item.get("risk_level")
                    if severity not in SEVERITIES:
                        severity = "medium"
                    status = "needs_review" if _severe(severity) else "open"
                    loc = item.get("source_location") or {}
                    snippet = loc.get("snippet")
                    session.add(ContractRiskItem(
                        organization_id=org_id,
                        user_id=review.user_id,
                        case_id=review.case_id,
                        review_id=review.id,
                        category=item.get("clause_type") or "other",
                        severity=severity,
                        title=item.get("label"),
                        summary=item.get("description"),
                        evidence_json=json.dumps(loc, ensure_ascii=False),
                        recommendation=item.get("suggestion"),
                        original_text_excerpt=str(snippet)[:1000] if snippet else None,
                        source="model",
                        status=status,
                        review_status=status,
                    ))
            if not refs_backfilled:
                for ref in _json_list(review.references_json):
                    _ref_to_reference(session, ref, org_id=org_id, user_id=review.user_id,
                                      case_id=review.case_id)

        # 2) legal_facts + legal_references from consultations
        for consultation in session.query(LegalConsultation).all():
            org_id = case_org.get(consultation.case_id) if consultation.case_id else None
            if session.query(LegalFact).filter(LegalFact.consultation_id == consultation.id).count() == 0:
                for fact in _json_list(consultation.known_facts_json):
                    if isinstance(fact, str) and fact.strip():
                        session.add(LegalFact(
                            organization_id=org_id, user_id=consultation.user_id,
                            case_id=consultation.case_id, consultation_id=consultation.id,
                            fact_type="known", statement=fact, source="model",
                        ))
                for fact in _json_list(consultation.missing_facts_json):
                    if isinstance(fact, str) and fact.strip():
                        session.add(LegalFact(
                            organization_id=org_id, user_id=consultation.user_id,
                            case_id=consultation.case_id, consultation_id=consultation.id,
                            fact_type="missing", statement=fact, source="model",
                        ))
            if not refs_backfilled:
                for ref in _json_list(consultation.references_json):
                    _ref_to_reference(session, ref, org_id=org_id, user_id=consultation.user_id,
                                      case_id=consultation.case_id)

        # 3) legal_references from drafts
        if not refs_backfilled:
            for draft in session.query(LegalDraft).all():
                org_id = case_org.get(draft.case_id) if draft.case_id else None
                for ref in _json_list(draft.references_json):
                    _ref_to_reference(session, ref, org_id=org_id, user_id=draft.user_id,
                                      case_id=draft.case_id)

        session.commit()
    finally:
        session.close()


def upgrade() -> None:
    # ── 扩展既有表：全部加可空列（batch_alter_table 跨方言）────────────────────
    with op.batch_alter_table("legal_sources") as batch:
        batch.add_column(sa.Column("expiration_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("applicability_scope", sa.Text(), nullable=True))
        batch.add_column(sa.Column("canonical_identifier", sa.String(128), nullable=True))

    with op.batch_alter_table("legal_review_actions") as batch:
        batch.add_column(sa.Column("target_version", sa.Integer(), nullable=True))

    for table in ("legal_consultations", "legal_contract_reviews", "legal_drafts"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("model_snapshot_json", sa.Text(), nullable=True))
            batch.add_column(sa.Column("reviewed_version", sa.Integer(), nullable=True))
    for table in ("legal_contract_reviews", "legal_drafts"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("is_final", sa.Integer(), nullable=False, server_default="0"))

    # ── 新表（有 FK 依赖顺序：claims 先于 evidences/references）────────────────
    _create_legal_facts()
    _create_legal_claims()
    _create_legal_evidences()
    _create_legal_references()
    _create_contract_risk_items()

    # ── 幂等回填 ──────────────────────────────────────────────────────────────
    bind = op.get_bind()
    _backfill(bind)


def downgrade() -> None:
    op.drop_index("ix_contract_risk_items_status", table_name="contract_risk_items")
    op.drop_index("ix_contract_risk_items_severity", table_name="contract_risk_items")
    op.drop_index("ix_contract_risk_items_review_id", table_name="contract_risk_items")
    op.drop_index("ix_contract_risk_items_case_id", table_name="contract_risk_items")
    op.drop_index("ix_contract_risk_items_user_id", table_name="contract_risk_items")
    op.drop_table("contract_risk_items")

    op.drop_index("ix_legal_references_source_id", table_name="legal_references")
    op.drop_index("ix_legal_references_claim_id", table_name="legal_references")
    op.drop_index("ix_legal_references_case_id", table_name="legal_references")
    op.drop_index("ix_legal_references_user_id", table_name="legal_references")
    op.drop_table("legal_references")

    op.drop_index("ix_legal_evidences_fact_id", table_name="legal_evidences")
    op.drop_index("ix_legal_evidences_claim_id", table_name="legal_evidences")
    op.drop_index("ix_legal_evidences_case_id", table_name="legal_evidences")
    op.drop_index("ix_legal_evidences_user_id", table_name="legal_evidences")
    op.drop_table("legal_evidences")

    op.drop_index("ix_legal_claims_risk_item_id", table_name="legal_claims")
    op.drop_index("ix_legal_claims_status", table_name="legal_claims")
    op.drop_index("ix_legal_claims_claim_type", table_name="legal_claims")
    op.drop_index("ix_legal_claims_source", table_name="legal_claims")
    op.drop_index("ix_legal_claims_case_id", table_name="legal_claims")
    op.drop_index("ix_legal_claims_user_id", table_name="legal_claims")
    op.drop_table("legal_claims")

    op.drop_index("ix_legal_facts_fact_type", table_name="legal_facts")
    op.drop_index("ix_legal_facts_consultation_id", table_name="legal_facts")
    op.drop_index("ix_legal_facts_case_id", table_name="legal_facts")
    op.drop_index("ix_legal_facts_user_id", table_name="legal_facts")
    op.drop_table("legal_facts")

    for table in ("legal_consultations", "legal_contract_reviews", "legal_drafts"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("reviewed_version")
            batch.drop_column("model_snapshot_json")
    for table in ("legal_contract_reviews", "legal_drafts"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("is_final")

    with op.batch_alter_table("legal_review_actions") as batch:
        batch.drop_column("target_version")

    with op.batch_alter_table("legal_sources") as batch:
        batch.drop_column("canonical_identifier")
        batch.drop_column("applicability_scope")
        batch.drop_column("expiration_date")
