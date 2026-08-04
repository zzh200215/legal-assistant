"""Phase 14 — 安全审计 / 通知偏好 / 引导进度"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class SecurityAuditEvent(Base):
    __tablename__ = "security_audit_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True,
                              comment="NULL=系统级事件")
    event_type = Column(String(64), nullable=False, index=True,
                        comment="login / permission_change / export / portal_access / key_op / sign_callback / admin_view")
    actor_type = Column(String(16), nullable=False,
                        comment="user / system / portal_visitor / api_key")
    actor_id = Column(String(64), nullable=True, comment="user_id / api_key前缀等，已脱敏")
    target_type = Column(String(64), nullable=True, comment="资源类型")
    target_id = Column(String(64), nullable=True, comment="资源ID，已脱敏")
    result = Column(String(16), nullable=False, comment="success / failure / blocked")
    detail_json_hash = Column(String(64), nullable=True, comment="详情摘要哈希，不存敏感明文")
    occurred_at = Column(DateTime(timezone=True), nullable=False, index=True)
    # 哈希链：保证不可篡改
    seq_no = Column(Integer, nullable=False, unique=True, index=True, autoincrement=False,
                    comment="全局顺序号，写入后不可修改")
    prev_hash = Column(String(64), nullable=True, comment="前一条事件的current_hash")
    current_hash = Column(String(64), nullable=False, comment="本条事件哈希，校验完整性用")


class LegalNotificationPreference(Base):
    __tablename__ = "legal_notification_preferences"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True,
                        comment="deadline / approval / invoice / sign / portal / all")
    channels_json = Column(Text, nullable=True, comment="启用的通知渠道JSON数组：[site/email/wechat/feishu]")
    mute_start = Column(String(5), nullable=True, comment="静默开始时间 HH:MM")
    mute_end = Column(String(5), nullable=True, comment="静默结束时间 HH:MM")
    timezone = Column(String(64), nullable=False, default="Asia/Shanghai")
    delegate_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, comment="代理接收人")
    summary_frequency = Column(String(16), nullable=True,
                               comment="none / daily / weekly，低优先级汇总发送频率")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalNotificationPolicy(Base):
    __tablename__ = "legal_notification_policies"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    escalation_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, comment="升级接收人")
    advance_days_json = Column(Text, nullable=True, comment="提前提醒天数JSON数组，如[90,30,7]")
    is_active = Column(Integer, nullable=False, default=1)
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class LegalNotificationEvent(Base):
    __tablename__ = "legal_notification_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    body = Column(Text, nullable=True)
    channel = Column(String(16), nullable=False, comment="site / email / wechat / feishu")
    status = Column(String(16), nullable=False, default="pending", index=True,
                    comment="pending / sent / delivered / read / acknowledged / failed / escalated")
    reference_type = Column(String(64), nullable=True, comment="deadline / invoice / sign_request / ...")
    reference_id = Column(Integer, nullable=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OrganizationOnboardingProgress(Base):
    __tablename__ = "organization_onboarding_progress"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False,
                              unique=True, index=True)
    user_role = Column(String(32), nullable=True,
                       comment="引导时选择的角色：solo_lawyer / firm_admin / enterprise_legal")
    completed_steps_json = Column(Text, nullable=True, comment="已完成步骤JSON数组")
    skipped_steps_json = Column(Text, nullable=True, comment="已跳过步骤JSON数组")
    version = Column(String(16), nullable=False, default="v3.0", comment="引导版本")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
