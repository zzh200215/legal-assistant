"""Celery 任务运行台账：每个关键异步任务的失败/重试上下文。

- task_runs 是「多 run 状态台账」，不做唯一约束——幂等去重由 idempotency_keys 承担，
  同一业务键的多次 run（重试/取代）都合法保留，形成可追溯历史。
- error_message 只存脱敏后的错误文本；不存完整合同正文/邮件/账单/PII。
"""

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from app.core.database import Base


class TaskRun(Base):
    __tablename__ = "task_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    task_id = Column(String(128), nullable=False, index=True, comment="Celery task id")
    task_name = Column(String(128), nullable=False, index=True)
    scope = Column(String(32), nullable=False, default="task",
                   comment="document / connector / notification / billing / email / ...")
    queue = Column(String(32), nullable=True)
    business_key = Column(String(256), nullable=True, index=True,
                          comment="document_id / connector_id / job_id ...")
    idempotency_key = Column(String(128), nullable=True, index=True)
    tenant_id = Column(Integer, nullable=True, index=True, comment="organization_id")
    status = Column(String(16), nullable=False, default="running", index=True,
                    comment="running / retrying / succeeded / failed")
    error_code = Column(String(64), nullable=True, comment="稳定业务错误码")
    error_message = Column(Text, nullable=True, comment="脱敏后的错误文本")
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=True)
    checkpoint_json = Column(Text, nullable=True, comment="阶段 checkpoint（断点恢复）")
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    trace_id = Column(String(64), nullable=True, index=True)
    request_id = Column(String(64), nullable=True, index=True, comment="API 入口 request_id（经 headers 传播）")
    agent_run_id = Column(Integer, nullable=True, index=True, comment="关联 Agent run")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
