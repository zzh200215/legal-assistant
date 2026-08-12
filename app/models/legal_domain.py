"""P1 法律业务统一模型：Claim / Evidence / Reference / Fact / ContractRiskItem。

设计原则（对应 P1 需求）：
- 事实、证据、主张（Claim）、法源引用、合同风险项各自独立成表，可查询、可关联、可审核；
- 既有的 JSON 列（risks_json / references_json / known_facts_json 等）保留为不可变快照与
  向后兼容层，结构化表是旁路写入的增强/关联/审核层，二者不互相覆盖；
- 每个实体带 organization_id（从 case 派生，可空）/ user_id / case_id，权限按 org+case+user 过滤；
- confidence 默认 NULL：模型/规则无法可靠给出精确支持度时不伪造分数；
- 敏感内容（合同原文片段等）只保存受限 excerpt，不落完整正文。

对外键说明：claim_id 在 evidences / references 上做 Claim -> Evidence -> Reference 链；
source_type + source_id 多态回指 consultation / contract_review / draft 原行（与既有
LegalDocumentVersion / LegalReviewAction 的多态 target 约定一致）。
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text, func

from app.core.database import Base


class LegalFact(Base):
    """事实陈述：已知事实 / 待确认事实 / 缺失事实。"""

    __tablename__ = "legal_facts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True,
                             comment="从关联 case 派生；NULL=未挂案件的个人数据")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    consultation_id = Column(Integer, ForeignKey("legal_consultations.id"), nullable=True, index=True)
    fact_type = Column(String(16), nullable=False, default="known", index=True,
                       comment="known / missing / to_confirm")
    statement = Column(Text, nullable=False, comment="事实表述")
    source = Column(String(16), nullable=False, default="model", comment="model / human")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalEvidence(Base):
    """支撑/反驳/待核验事实或主张的证据，必须可定位到具体文档、条款、法条或外部来源。"""

    __tablename__ = "legal_evidences"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    claim_id = Column(Integer, ForeignKey("legal_claims.id"), nullable=True, index=True,
                      comment="所属主张（Claim -> Evidence）")
    fact_id = Column(Integer, ForeignKey("legal_facts.id"), nullable=True, index=True,
                     comment="所支撑/反驳的事实（Fact <-> Evidence）")
    kind = Column(String(24), nullable=False, default="support", index=True,
                  comment="support / against / needs_verification")
    description = Column(Text, nullable=True)
    extraction_method = Column(String(64), nullable=True, comment="model / rule / human / manual")
    source_type = Column(String(24), nullable=False, default="document",
                         comment="document / clause / reference / external")
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True, index=True)
    contract_clause_id = Column(Integer, ForeignKey("legal_contract_clauses.id"), nullable=True, index=True)
    source_id = Column(Integer, ForeignKey("legal_sources.id"), nullable=True, index=True,
                       comment="法源，与 article_id 一起定位到具体条文")
    article_id = Column(Integer, ForeignKey("legal_articles.id"), nullable=True, index=True)
    loc_json = Column(Text, nullable=True, comment="定位信息 JSON：page/paragraph/start/end/clause")
    content_hash = Column(String(64), nullable=True, comment="原文内容 SHA-256，保证可回溯")
    excerpt = Column(String(1000), nullable=True, comment="受限原文摘录，不保存完整证据内容")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalClaim(Base):
    """法律主张：法律结论 / 风险提示 / 待确认事实，统一结论层级。"""

    __tablename__ = "legal_claims"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    # 多态回指原工作台行（consultation / contract_review / draft）。
    source_type = Column(String(32), nullable=False, index=True)
    source_id = Column(Integer, nullable=False, index=True)
    # 关联合同风险项（无 FK，指向 contract_risk_items，与项目多态/无FK列惯例一致）。
    risk_item_id = Column(Integer, nullable=True, index=True)
    claim_type = Column(String(24), nullable=False, index=True,
                        comment="legal_conclusion / risk_warning / fact_to_confirm")
    title = Column(String(256), nullable=True)
    summary = Column(Text, nullable=True, comment="结构化摘要")
    statement = Column(Text, nullable=True, comment="用户可读表述")
    # 置信度/支持程度：无法可靠提供时必须为 NULL，不伪造精确分数。
    confidence = Column(Numeric(5, 4), nullable=True)
    assumptions_json = Column(Text, nullable=True, comment="适用前提/假设，JSON 数组")
    limitations_json = Column(Text, nullable=True, comment="限制/不确定性，JSON 数组")
    status = Column(String(24), nullable=False, default="draft", index=True,
                    comment="draft / pending_review / needs_review / approved / changes_requested / rejected / unsupported / superseded")
    review_status = Column(String(24), nullable=True,
                           comment="审核队列镜像：needs_review=需人工审核，空/其他=不在队列")
    source = Column(String(16), nullable=False, default="model", comment="model / rule / human")
    model_snapshot_json = Column(Text, nullable=True,
                                 comment="模型/提示词版本、输入 hash、生成时间快照，JSON")
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalReference(Base):
    """指向 LegalSource / LegalArticle 的可定位引用，记录分析时的版本快照与适用性判定。"""

    __tablename__ = "legal_references"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    claim_id = Column(Integer, ForeignKey("legal_claims.id"), nullable=True, index=True,
                      comment="所属主张（Claim -> Reference）")
    source_id = Column(Integer, ForeignKey("legal_sources.id"), nullable=False, index=True)
    article_id = Column(Integer, ForeignKey("legal_articles.id"), nullable=True, index=True)
    article_number = Column(String(32), nullable=True, comment="条文号，如'第40条'")
    chapter = Column(String(64), nullable=True)
    section = Column(String(64), nullable=True)
    paragraph = Column(Integer, nullable=True)
    citation_text = Column(String(512), nullable=True, comment="引用表述/原文摘录")
    analysis_date = Column(DateTime(timezone=True), nullable=True, comment="分析时点")
    jurisdiction = Column(String(128), nullable=True, comment="分析目标地域")
    applicable = Column(Integer, nullable=True, comment="1=适用 0=不适用 NULL=适用性未知")
    applicability_note = Column(String(512), nullable=True, comment="不适用/待核验原因")
    source_version_snapshot = Column(String(512), nullable=True,
                                     comment="引用时法源版本/状态/生效日期快照，JSON")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ContractRiskItem(Base):
    """合同审查风险项，统一结构化为可审核实体。"""

    __tablename__ = "contract_risk_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    contract_id = Column(Integer, ForeignKey("legal_contracts.id"), nullable=True, index=True)
    contract_version_id = Column(Integer, ForeignKey("legal_contract_versions.id"), nullable=True, index=True)
    review_id = Column(Integer, ForeignKey("legal_contract_reviews.id"), nullable=False, index=True,
                       comment="所属合同审查")
    clause_id = Column(Integer, ForeignKey("legal_contract_clauses.id"), nullable=True, index=True)
    category = Column(String(32), nullable=False, index=True,
                      comment="payment/delivery/breach/compensation/confidentiality/ip/termination/dispute_resolution/other")
    severity = Column(String(16), nullable=False, default="medium", index=True,
                      comment="low / medium / high / critical")
    title = Column(String(256), nullable=True)
    summary = Column(Text, nullable=True)
    evidence_json = Column(Text, nullable=True, comment="结构化证据/原文定位，JSON")
    recommendation = Column(Text, nullable=True)
    suggested_revision = Column(Text, nullable=True, comment="建议修改文本")
    original_text_excerpt = Column(String(1000), nullable=True, comment="受限原文摘录")
    legal_basis_json = Column(Text, nullable=True, comment="法条依据引用数组，JSON")
    source = Column(String(16), nullable=False, default="model", comment="model / rule / human")
    status = Column(String(24), nullable=False, default="open", index=True,
                    comment="open / needs_review / accepted / mitigated / dismissed")
    review_status = Column(String(24), nullable=True, comment="审核队列镜像：needs_review=在审核队列")
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
