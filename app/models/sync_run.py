"""连接器同步运行台账（表 connector_sync_jobs）。

复用 0013/0014 遗留建表并补齐同步可靠性列：cursor / checkpoint / 增量 hash /
计数 / 错误分类 / 重试 / 租约（崩溃恢复）。cursor 仅对应批次成功提交后推进。
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class SyncRun(Base):
    __tablename__ = "connector_sync_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    connector_id = Column(Integer, ForeignKey("external_connectors.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True,
                    comment="pending / running / succeeded / failed")
    sync_mode = Column(String(32), nullable=False, default="manual")
    result_summary = Column(Text, nullable=True, comment="结果摘要，不含敏感内容")
    error_message = Column(Text, nullable=True, comment="脱敏错误文本")
    result_detail_json = Column(Text, nullable=True, comment="导入/跳过计数等 JSON")
    cursor_json = Column(Text, nullable=True, comment="已提交游标（本 run 视角）")
    checkpoint_json = Column(Text, nullable=True, comment="批内 checkpoint（下批起点）")
    source_version = Column(String(128), nullable=True, comment="增量 ETag / updated_at / hash")
    processed = Column(Integer, nullable=False, default=0)
    succeeded = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String(64), nullable=True, comment="稳定业务错误码")
    attempt = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    idempotency_key = Column(String(128), nullable=True, index=True)
    lease_owner = Column(String(128), nullable=True, comment="持有 worker/run 标识")
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
