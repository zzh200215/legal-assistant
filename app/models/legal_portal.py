"""Phase 11 — 门户 / 案件成员 / 进度更新 / 关键日期领域模型"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class LegalDeadline(Base):
    __tablename__ = "legal_deadlines"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    contract_id = Column(Integer, nullable=True, index=True, comment="关联合同台账ID（legal_contracts）")
    deadline_type = Column(String(32), nullable=False, index=True,
                           comment="hearing/defense/appeal/performance/payment/expiry/custom")
    deadline_at = Column(DateTime(timezone=True), nullable=False)
    timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True, comment="责任人")
    status = Column(String(16), nullable=False, default="active", index=True,
                    comment="active / completed / cancelled / due")
    description = Column(Text, nullable=True)
    reminder_offsets_json = Column(Text, nullable=True,
                                   comment="提醒偏移天数JSON数组，如[7,3,1]，默认[7,3,1]")
    source_milestone_id = Column(Integer, nullable=True, comment="来源合同里程碑ID（AI提取）")
    is_historical = Column(Integer, nullable=False, default=0, comment="1=补录历史日期，已确认")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalPortalLink(Base):
    __tablename__ = "legal_portal_links"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True, comment="令牌SHA-256哈希")
    token_prefix = Column(String(8), nullable=False, comment="令牌前缀，仅用于展示标识")
    client_email = Column(String(256), nullable=True, comment="客户邮箱，用于验证码校验")
    expires_at = Column(DateTime(timezone=True), nullable=True, comment="NULL表示永久，仍可被撤销")
    is_permanent = Column(Integer, nullable=False, default=0)
    max_access_count = Column(Integer, nullable=True, comment="NULL=不限次数")
    access_count = Column(Integer, nullable=False, default=0)
    require_email_verification = Column(Integer, nullable=False, default=1)
    aggregate_case = Column(Integer, nullable=False, default=0,
                            comment="1=聚合该案件全部已发布客户可见内容（一个案件一个URL，#79 P2）")
    status = Column(String(16), nullable=False, default="active", index=True,
                    comment="active / expired / revoked / access_limited")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_accessed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalPortalLinkItem(Base):
    __tablename__ = "legal_portal_link_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    portal_link_id = Column(Integer, ForeignKey("legal_portal_links.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    # item_type: progress / document / invoice / sign_request
    item_type = Column(String(32), nullable=False)
    item_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalPortalAccessLog(Base):
    __tablename__ = "legal_portal_access_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    portal_link_id = Column(Integer, ForeignKey("legal_portal_links.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    accessed_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    ip_hash = Column(String(64), nullable=True, comment="IP哈希，不存明文")
    user_agent_summary = Column(String(256), nullable=True)
    action = Column(String(32), nullable=False, comment="view / download / verify / payment")
    resource_type = Column(String(32), nullable=True)
    resource_id = Column(Integer, nullable=True)
    result = Column(String(16), nullable=False, default="success", comment="success / denied / error")


class LegalCaseMember(Base):
    __tablename__ = "legal_case_members"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_role = Column(String(32), nullable=False,
                       comment="owner / collaborator / viewer / client_contact")
    granted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    granted_at = Column(DateTime(timezone=True), server_default=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)


class LegalCaseProgressUpdate(Base):
    __tablename__ = "legal_case_progress_updates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    title = Column(String(128), nullable=False)
    body = Column(Text, nullable=False, comment="正文，1-5000字")
    next_steps = Column(Text, nullable=True, comment="下步计划，1-1000字")
    visibility = Column(String(16), nullable=False, default="internal",
                        comment="internal / client_visible")
    status = Column(String(16), nullable=False, default="draft", index=True,
                    comment="draft / pending_review / published / withdrawn")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    withdrawn_at = Column(DateTime(timezone=True), nullable=True)
    withdraw_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalPortalFeedback(Base):
    """客户门户反馈：客户通过链接对律师服务的 👍/👎 评价（P3，独立表而非落到 AI 输出列）。"""

    __tablename__ = "legal_portal_feedback"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    portal_link_id = Column(Integer, ForeignKey("legal_portal_links.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=False, index=True)
    score = Column(Integer, nullable=False, comment="1=有帮助 / -1=待改进")
    note = Column(Text, nullable=True, comment="待改进时的补充说明，≤500字")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LegalCaseProgressRead(Base):
    __tablename__ = "legal_case_progress_reads"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    progress_update_id = Column(Integer, ForeignKey("legal_case_progress_updates.id",
                                                     ondelete="CASCADE"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=False, index=True)
    reader_type = Column(String(16), nullable=False, comment="portal_visitor / org_member")
    reader_id = Column(Integer, nullable=True, comment="org_member时为user_id")
    token_hash = Column(String(64), nullable=True, comment="portal_visitor时为令牌哈希")
    read_at = Column(DateTime(timezone=True), server_default=func.now())
