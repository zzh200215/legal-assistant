"""Phase 14 — 安全审计 / 通知偏好 / 引导进度 / 通知模板与投递可靠性"""

from sqlalchemy import (
    Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)

from app.core.database import Base


class SecurityAuditEvent(Base):
    """P1 通用不可篡改审计事件（原安全审计哈希链扩展）。

    - 追加式写入：业务代码仅经 security_audit_service.write_event，无 UPDATE/DELETE 路径。
    - hash chain：seq_no 全局有序（Redis INCR），prev_hash/current_hash 校验完整性。
    - schema_version：1=旧公式（seq|type|actor|time|prev），2=纳入 action/resource/trace 等字段；
      verify_chain 按版本分别重算，存量行零迁移成本。
    """

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
    # ── P1 扩展字段（schema_version=2 起参与哈希）─────────────────────────────
    audit_id = Column(String(64), nullable=True, index=True, comment="审计记录唯一 ID（缺省为 seq_no 字符串）")
    action = Column(String(64), nullable=True, comment="动作名（稳定枚举/event_name）")
    resource_version = Column(String(64), nullable=True, comment="资源版本号/乐观锁版本")
    request_id = Column(String(64), nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
    task_id = Column(String(128), nullable=True, index=True)
    agent_run_id = Column(Integer, nullable=True, index=True)
    decision = Column(String(16), nullable=True, comment="allow / deny / review / pending")
    reason_code = Column(String(64), nullable=True, comment="稳定原因码（有限枚举）")
    sanitized_metadata = Column(Text, nullable=True, comment="脱敏后的附加元数据（JSON），禁止正文")
    schema_version = Column(Integer, nullable=False, default=1, comment="哈希公式版本")
    archived_at = Column(DateTime(timezone=True), nullable=True, index=True,
                         comment="归档时间（保留任务归档后标记，默认不物理删除）")


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
    """通知事件：同时承担「通知 Outbox」角色（业务记录与投递记录合一）。

    状态机（requested/pending → approved → sending → sent/delivered，
    failed → requested / dead_letter）由 notification_service 集中校验。
    投递列（attempt/next_retry_at/claimed_by/claim_expires_at）支持 worker
    原子领取与崩溃回收；idempotency_key 提供数据库级重复发送防护。
    """
    __tablename__ = "legal_notification_events"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_legal_notification_events_idempotency_key"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    body = Column(Text, nullable=True)
    channel = Column(String(16), nullable=False, comment="site / email / wechat / feishu")
    status = Column(String(16), nullable=False, default="pending", index=True,
                    comment="pending/requested / approved / rejected / sending / sent / delivered / read / acknowledged / failed / escalated / dead_letter")
    reference_type = Column(String(64), nullable=True, comment="deadline / invoice / sign_request / ...")
    reference_id = Column(Integer, nullable=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # 投递可靠性（Outbox 领取 / 重试 / 死信）
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    claimed_by = Column(String(128), nullable=True, comment="持有投递的 worker/run 标识")
    claim_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    error_code = Column(String(64), nullable=True, comment="稳定业务错误码，脱敏")
    sanitized_error_message = Column(Text, nullable=True)
    provider_message_id = Column(String(256), nullable=True)
    # 邮件通知回链：实际 SMTP 投递由 EmailSendRequest 承担
    email_send_request_id = Column(Integer, ForeignKey("email_send_requests.id"), nullable=True, index=True)
    # 模板与渲染
    template_key = Column(String(128), nullable=True)
    template_version = Column(Integer, nullable=True)
    locale = Column(String(16), nullable=True)
    # 幂等键（UNIQUE 约束兜底重复创建/重放）
    idempotency_key = Column(String(128), nullable=True, index=True)
    # P1 链路关联：由统一上下文写入（API/Celery headers 传播），缺失为 NULL
    trace_id = Column(String(64), nullable=True, index=True)
    request_id = Column(String(64), nullable=True, index=True)


class NotificationTemplate(Base):
    """统一通知模板：按 channel + template_key + locale + version 精确选取。

    - 版本不可原地覆盖：内容变化创建新版本，历史投递可追溯原模板。
    - params_schema_json 校验渲染参数，禁止未校验的任意变量注入。
    - status：draft / active / retired。
    """
    __tablename__ = "notification_templates"
    __table_args__ = (
        UniqueConstraint("channel", "template_key", "locale", "version",
                         name="uq_notification_templates_channel_key_locale_version"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    channel = Column(String(16), nullable=False, index=True, comment="site / email / webhook / sms")
    template_key = Column(String(128), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    locale = Column(String(16), nullable=False, default="default", comment="zh-CN / zh / default")
    subject_template = Column(String(512), nullable=True, comment="邮件主题模板（{{param}} 占位）")
    body_template = Column(Text, nullable=False,
                           comment="正文/载荷模板；body_format=layout 时存 JSON 覆写（lead/action_label/footnote）")
    body_format = Column(String(16), nullable=False, default="text", server_default="text",
                         comment="text=纯文本 / html=整段 HTML / layout=内置版式字段覆写")
    params_schema_json = Column(Text, nullable=True, comment="参数 JSON Schema")
    status = Column(String(16), nullable=False, default="draft", index=True,
                    comment="draft / active / retired")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    content_hash = Column(String(64), nullable=False, comment="模板内容 SHA-256")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OutboxSnapshot(Base):
    """投递积压快照（定时采样），用于画真实的积压曲线。

    为什么单独一张表：积压是「状态」不是「增量」，清点类指标无法从事件明细反推
    历史值；按 5 分钟采样一行，成本可控（每天 288 行/组织），保留期由
    ``OUTBOX_SNAPSHOT_RETENTION_DAYS`` 控制。

    ``organization_id`` 为 NULL 表示平台级（全部组织之和）；每个有管理员的组织
    另存一行，供组织维度看板使用。
    """

    __tablename__ = "outbox_snapshots"
    __table_args__ = (
        Index("ix_outbox_snapshots_org_captured", "organization_id", "captured_at"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    captured_at = Column(DateTime(timezone=True), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True,
                             comment="NULL=平台级")
    notification_backlog = Column(Integer, nullable=False, default=0,
                                  comment="pending + approved + sending")
    notification_dead_letter = Column(Integer, nullable=False, default=0)
    notification_retry_pending = Column(Integer, nullable=False, default=0)
    notification_stale_claims = Column(Integer, nullable=False, default=0)
    email_backlog = Column(Integer, nullable=False, default=0)
    email_dead_letter = Column(Integer, nullable=False, default=0)
    email_retry_pending = Column(Integer, nullable=False, default=0)
    email_stale_claims = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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
