"""Phase 12 — 合同台账 / 版本 / 条款 / 里程碑 / 电子签名 / 审查策略"""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func, text

from app.core.database import Base
from app.core.encryption import EncryptedText


class LegalContract(Base):
    __tablename__ = "legal_contracts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    contract_no = Column(String(64), nullable=False, index=True, comment="合同编号，组织内唯一")
    title = Column(String(256), nullable=False)
    counterparty = Column(String(256), nullable=True)
    contract_type = Column(String(64), nullable=True,
                           comment="purchase/service/labor/loan/lease/nda/other")
    status = Column(String(16), nullable=False, default="active", index=True,
                    comment="active / signed / terminated / expired / voided")
    current_version_id = Column(Integer, nullable=True, comment="当前版本ID（legal_contract_versions.id）")
    responsible_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    risk_level = Column(String(16), nullable=True, comment="low / medium / high")
    description = Column(EncryptedText, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    # 乐观锁版本号（version_id_col）：合同状态/责任人由多方并发修改时防丢失更新。
    version = Column(Integer, nullable=False, server_default=text("1"), default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __mapper_args__ = {"version_id_col": version}
    # 台账 keyset 排序/尾段查询用（step46）；id 已在主键里，显式写进复合索引让排序完全走索引。
    __table_args__ = (
        Index("ix_legal_contracts_org_updated_at", "organization_id", "updated_at", "id"),
        Index("ix_legal_contracts_org_created_at", "organization_id", "created_at", "id"),
        Index("ix_legal_contracts_org_pk", "organization_id", "id"),
    )


class LegalContractVersion(Base):
    __tablename__ = "legal_contract_versions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("legal_contracts.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False, comment="版本号，同合同内递增")
    # 来源类型：document / text_snapshot / contract_review
    source_type = Column(String(32), nullable=False, default="document")
    source_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    source_review_id = Column(Integer, ForeignKey("legal_contract_reviews.id"), nullable=True)
    content_hash = Column(String(64), nullable=True, comment="文本快照SHA-256，保证不可变性")
    text_snapshot = Column(EncryptedText, nullable=True, comment="纯文本快照，与原文件分离存储")
    parse_status = Column(String(24), nullable=False, default="uploading", index=True,
                          comment="uploading / parsing / ready / needs_confirmation / failed")
    parse_confidence = Column(Numeric(5, 4), nullable=True, comment="0.0000-1.0000")
    is_current = Column(Integer, nullable=False, default=0, index=True)
    version_note = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalContractClause(Base):
    __tablename__ = "legal_contract_clauses"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    contract_version_id = Column(Integer, ForeignKey("legal_contract_versions.id",
                                                      ondelete="CASCADE"), nullable=False, index=True)
    clause_no = Column(String(32), nullable=True, comment="条款编号，如'第3条'")
    clause_title = Column(String(256), nullable=True)
    content = Column(EncryptedText, nullable=False)
    chapter = Column(String(128), nullable=True)
    sequence = Column(Integer, nullable=False, default=0)
    parse_confidence = Column(Numeric(5, 4), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalContractReviewSummary(Base):
    """合同台账的审查摘要（物化），让台账筛选/分页能下推数据库。

    为什么单独一张表而不加在 ``legal_contracts`` 上：合同表有乐观锁
    (``version_id_col``)，写摘要会顶高版本号，导致并发编辑合同的人拿到 409。
    摘要只由版本↔审查的写入路径刷新，台账读写都不触碰合同行本身。

    语义与 step36 的读时派生一致：取该合同**最新**带有 ``source_review_id`` 的版本，
    再取对应审查的状态与 ``risks_json`` 计数；没有审查时**删除本行**（台账显示未审查）。
    """

    __tablename__ = "legal_contract_review_summaries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("legal_contracts.id", ondelete="CASCADE"),
                         nullable=False, unique=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    review_id = Column(Integer, ForeignKey("legal_contract_reviews.id"), nullable=True)
    review_status = Column(String(32), nullable=False, index=True)
    review_version = Column(Integer, nullable=True)
    risks_total = Column(Integer, nullable=False, default=0)
    # ``risks_high`` 含 critical（与 step36 口径一致）；``risks_critical`` 是其子集，
    # ``risks_high + risks_medium + risks_low == risks_total``（未知严重度按 medium 计）。
    risks_high = Column(Integer, nullable=False, default=0, server_default=text("0"))
    risks_critical = Column(Integer, nullable=False, default=0, server_default=text("0"))
    risks_medium = Column(Integer, nullable=False, default=0, server_default=text("0"))
    risks_low = Column(Integer, nullable=False, default=0, server_default=text("0"))
    risks_unanchored = Column(Integer, nullable=False, default=0)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_updated_at = Column(DateTime(timezone=True), nullable=True)
    refreshed_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 按审查时间排序时让摘要表驱动查询，避免对 JOIN 结果 filesort（step46）。
    __table_args__ = (
        Index("ix_legal_contract_review_summaries_org_reviewed",
              "organization_id", "reviewed_at", "contract_id"),
        Index("ix_legal_contract_review_summaries_org_review_updated",
              "organization_id", "review_updated_at", "contract_id"),
    )


class LegalContractMilestone(Base):
    __tablename__ = "legal_contract_milestones"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("legal_contracts.id"), nullable=False, index=True)
    contract_version_id = Column(Integer, ForeignKey("legal_contract_versions.id"), nullable=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    milestone_type = Column(String(32), nullable=False, index=True,
                            comment="expiry / renewal / payment / performance / custom")
    raw_text = Column(Text, nullable=True, comment="AI提取的原始文本片段")
    standard_date = Column(DateTime(timezone=True), nullable=True, comment="标准化日期")
    source_clause_no = Column(String(32), nullable=True)
    confidence = Column(Numeric(5, 4), nullable=True)
    status = Column(String(24), nullable=False, default="pending_confirmation", index=True,
                    comment="pending_confirmation / confirmed / cancelled / needs_confirmation")
    confirmed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalSignRequest(Base):
    __tablename__ = "legal_sign_requests"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    contract_id = Column(Integer, ForeignKey("legal_contracts.id"), nullable=False, index=True)
    contract_version_id = Column(Integer, ForeignKey("legal_contract_versions.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    provider = Column(String(32), nullable=False, comment="fadada / esigncn / other")
    provider_request_id = Column(String(128), nullable=True, unique=True, index=True,
                                  comment="服务商签署单唯一ID")
    status = Column(String(16), nullable=False, default="draft", index=True,
                    comment="draft / sent / pending_sign / signed / rejected / expired / needs_attention")
    replaces_request_id = Column(Integer, ForeignKey("legal_sign_requests.id"), nullable=True,
                                  comment="拒签/过期后替代请求关联")
    initiated_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalSignParty(Base):
    __tablename__ = "legal_sign_parties"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sign_request_id = Column(Integer, ForeignKey("legal_sign_requests.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    name = Column(String(128), nullable=False)
    email_hash = Column(String(64), nullable=True, comment="邮箱哈希，不存明文")
    phone_masked = Column(String(32), nullable=True, comment="手机号脱敏展示")
    sign_order = Column(Integer, nullable=False, default=1, comment="签署顺序")
    status = Column(String(16), nullable=False, default="pending",
                    comment="pending / signed / rejected")
    signed_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    reject_reason = Column(Text, nullable=True, comment="脱敏后的拒签原因")
    provider_sign_id = Column(String(128), nullable=True, comment="服务商签署方ID")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalSignEvent(Base):
    __tablename__ = "legal_sign_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    sign_request_id = Column(Integer, ForeignKey("legal_sign_requests.id"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False, index=True,
                        comment="signed / rejected / expired / viewed / reminded")
    provider_event_id = Column(String(128), nullable=False, unique=True, index=True,
                               comment="服务商事件唯一ID，用于去重")
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    raw_payload_hash = Column(String(64), nullable=True, comment="原始回调摘要哈希")
    party_id = Column(Integer, ForeignKey("legal_sign_parties.id"), nullable=True)
    result = Column(String(16), nullable=False, comment="success / failed / pending")
    processed_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalReviewPolicy(Base):
    __tablename__ = "legal_review_policies"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    party_role = Column(String(16), nullable=False, default="unknown",
                        comment="party_a / party_b / platform / unknown")
    contract_type = Column(String(64), nullable=True)
    scenario = Column(String(128), nullable=True)
    amount_threshold_min = Column(Numeric(14, 2), nullable=True)
    amount_threshold_max = Column(Numeric(14, 2), nullable=True)
    risk_preference = Column(String(16), nullable=False, default="standard",
                             comment="strict / standard / lenient")
    required_clauses_json = Column(Text, nullable=True, comment="必审条款JSON数组")
    focus_points = Column(Text, nullable=True, comment="自定义关注点，2000字以内")
    is_active = Column(Integer, nullable=False, default=1, index=True)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalReviewPolicyVersion(Base):
    __tablename__ = "legal_review_policy_versions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    policy_id = Column(Integer, ForeignKey("legal_review_policies.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    version = Column(Integer, nullable=False)
    name = Column(String(128), nullable=False)
    config_snapshot = Column(Text, nullable=False, comment="策略快照JSON，不可变")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
