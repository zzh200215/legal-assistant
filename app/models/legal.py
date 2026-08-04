"""Legal-domain records used by the Law Intelligence MVP.

The models deliberately keep generated payloads as JSON text. This mirrors the
existing project's audit-oriented storage and lets us preserve the exact
evidence payload returned by an agent for later review/versioning.
"""

from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base
from app.core.encryption import EncryptedText


class LegalCase(Base):
    """法律案件 — Phase 9 Week 2

    一个劳动争议可能涉及：3次咨询、2份合同审查、1份仲裁申请书。
    案件作为顶层容器，关联所有相关工作记录。
    """

    __tablename__ = "legal_cases"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True, comment="案件创建人/负责人")
    title = Column(String(256), nullable=False, comment="案件名称，如'张三 vs XX公司 劳动争议'")
    case_type = Column(String(64), nullable=False, index=True, comment="labor_dispute / contract_dispute / private_lending / consumer_dispute / other")
    status = Column(String(32), nullable=False, default="in_progress", index=True, comment="in_progress / closed / archived")
    is_strict_mode = Column(Integer, nullable=False, default=0, comment="1=严格模式：仅案件成员可访问；0=普通模式：组织成员均可访问")
    client_name = Column(EncryptedText, nullable=True, comment="客户姓名（AES-256-GCM）")
    opposing_party = Column(EncryptedText, nullable=True, comment="对方当事人（AES-256-GCM）")
    description = Column(EncryptedText, nullable=True, comment="案情摘要（AES-256-GCM）")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalSource(Base):
    """法源资料 — V2.0 升级版

    从 demo 级的 title + content 简版，扩展为覆盖完整法规元数据、
    发布机关、条文级关联的生产级法源模型。
    """

    __tablename__ = "legal_sources"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    source_type = Column(String(32), nullable=False, index=True)  # statute/case/template/judicial_interpretation
    citation = Column(String(256), nullable=True)
    jurisdiction = Column(String(128), nullable=True)

    # V2.0 新增字段：法规元数据
    document_number = Column(String(64), nullable=True, comment="发文字号，如'主席令第65号'")
    promulgator = Column(String(128), nullable=True, comment="发布机关，如'全国人大常委会'")
    promulgation_date = Column(Date, nullable=True, comment="发布日期")

    effective_date = Column(Date, nullable=True)
    version = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False, default="active", index=True)

    # content 保持为摘要/简介，full_text 为全文
    content = Column(Text, nullable=False, comment="法源摘要/核心内容")
    full_text = Column(Text, nullable=True, comment="法规全文，与 content 分离存储")

    # V2.0 新增字段：标签与关联
    law_area_json = Column(Text, nullable=True, comment="法律领域标签，JSON数组")
    keywords_json = Column(Text, nullable=True, comment="关键词，JSON数组")
    amended_by_json = Column(Text, nullable=True, comment="被哪些后续法规修订，source_id数组")
    amends_json = Column(Text, nullable=True, comment="修订了哪些前序法规，source_id数组")

    # Phase 9：团队共享支持
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True,
                             comment="归属组织，NULL=个人法源库，非NULL=团队共享法源库")
    scope = Column(String(16), nullable=False, default="personal", index=True,
                   comment="personal=个人 | team=团队共享")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalArticle(Base):
    """法律条文 — 按条文号拆分，支持条文级精确定位与召回。

    一部法律（LegalSource）对应多条文（LegalArticle）。
    条文级检索比法源级检索精度更高，可定位到具体第 N 条。
    """

    __tablename__ = "legal_articles"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source_id = Column(Integer, ForeignKey("legal_sources.id", ondelete="CASCADE"), nullable=False, index=True)
    article_number = Column(String(32), nullable=False, comment="条文编号，如'第40条'、'第509条'")
    title = Column(String(256), nullable=True, comment="条文标题，如'无过失性辞退'")
    content = Column(Text, nullable=False, comment="条文正文")
    chapter = Column(String(64), nullable=True, comment="所属章，如'第四章 劳动合同的解除和终止'")
    section = Column(String(64), nullable=True, comment="所属节，如'第二节 用人单位单方解除'")
    sequence = Column(Integer, nullable=False, default=0, comment="条文在原法中的顺序号，用于排序")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalConsultation(Base):
    __tablename__ = "legal_consultations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    question = Column(Text, nullable=False)
    category = Column(String(64), nullable=False, index=True)
    known_facts_json = Column(Text, nullable=False, default="[]")
    missing_facts_json = Column(Text, nullable=False, default="[]")
    references_json = Column(Text, nullable=False, default="[]")
    advice = Column(Text, nullable=False)
    risk_level = Column(String(16), nullable=False, default="low", index=True)
    disclaimer_level = Column(String(16), nullable=True, default="low", comment="免责声明级别: low/medium/high")
    status = Column(String(32), nullable=False, default="draft", index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    feedback_score = Column(Integer, nullable=True, comment="用户评分：1=满意 -1=不满意")
    feedback_note = Column(Text, nullable=True, comment="用户反馈备注")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ContractReview(Base):
    __tablename__ = "legal_contract_reviews"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True, index=True)
    title = Column(String(256), nullable=False)
    content = Column(EncryptedText, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="pending_review", index=True)
    summary = Column(Text, nullable=False, default="")
    risks_json = Column(Text, nullable=False, default="[]")
    references_json = Column(Text, nullable=False, default="[]")
    # 审查发起时冻结，避免策略后续修改影响历史结论。
    review_policy_id = Column(Integer, nullable=True, index=True)
    review_policy_version = Column(Integer, nullable=True)
    review_policy_snapshot_json = Column(Text, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    feedback_score = Column(Integer, nullable=True, comment="用户评分：1=满意 -1=不满意")
    feedback_note = Column(Text, nullable=True, comment="用户反馈备注")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalDraft(Base):
    __tablename__ = "legal_drafts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    document_type = Column(String(64), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    fields_json = Column(Text, nullable=False, default="{}")
    missing_fields_json = Column(Text, nullable=False, default="[]")
    references_json = Column(Text, nullable=False, default="[]")
    content = Column(EncryptedText, nullable=False, default="")
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="draft", index=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    feedback_score = Column(Integer, nullable=True, comment="用户评分：1=满意 -1=不满意")
    feedback_note = Column(Text, nullable=True, comment="用户反馈备注")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalDocumentVersion(Base):
    """合同审查/文书草稿的历史版本快照。

    退回补充事实后用户修改重新提交时，旧版本内容会被固化到此表，
    避免覆盖丢失，支持版本对比和留痕审计（FL.md 6.2/6.4 版本留痕）。
    """

    __tablename__ = "legal_document_versions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    target_type = Column(String(32), nullable=False, index=True)  # contract_review / draft
    target_id = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    title = Column(String(256), nullable=True)
    content = Column(EncryptedText, nullable=False)
    status_at_snapshot = Column(String(32), nullable=False)
    snapshot_reason = Column(String(32), nullable=False, default="resubmit")  # resubmit / manual
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalApprovalChain(Base):
    """法律文书多级审批链 — Phase 9 Week 3

    一个审批链包含若干审批步骤（串行或并行）。
    chain_type: serial（一步一步逐级通过）| parallel（所有人同时审批，全部通过才完成）
    """

    __tablename__ = "legal_approval_chains"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    target_type = Column(String(32), nullable=False, index=True)  # contract_review / draft / consultation
    target_id = Column(Integer, nullable=False, index=True)
    chain_type = Column(String(16), nullable=False, default="serial", comment="serial | parallel")
    status = Column(String(32), nullable=False, default="pending", index=True,
                    comment="pending / in_progress / approved / rejected / timeout")
    current_step = Column(Integer, nullable=False, default=0, comment="串行模式下当前进行到第几步")
    timeout_hours = Column(Integer, nullable=True, comment="每步超时时间（小时），NULL=不限时")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalApprovalStep(Base):
    """审批链中的单个审批步骤

    每行代表一位审批人在某一步的审批动作。
    对于 serial 链，每步只有一行；对于 parallel 链，同一 step_order 可有多行。
    """

    __tablename__ = "legal_approval_steps"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    chain_id = Column(Integer, ForeignKey("legal_approval_chains.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    step_order = Column(Integer, nullable=False, default=0, comment="步骤序号（串行模式下严格递增）")
    approver_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    approver_role = Column(String(32), nullable=True, comment="期望角色标签，informational only")
    status = Column(String(16), nullable=False, default="pending",
                    comment="pending / approved / rejected / timeout")
    note = Column(Text, nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=True, comment="超时截止时间")
    acted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalReviewAction(Base):
    __tablename__ = "legal_review_actions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    target_type = Column(String(32), nullable=False, index=True)
    target_id = Column(Integer, nullable=False, index=True)
    action = Column(String(32), nullable=False)
    note = Column(Text, nullable=True)
    from_status = Column(String(32), nullable=True)
    to_status = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
