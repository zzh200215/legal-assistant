"""数据库归档运行台账：幂等 / 运行锁 / 审计。

每次按表归档在表内记录一行（running → completed/failed），作为锁与审计依据。
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func

from app.core.database import Base


class DatabaseArchiveRun(Base):
    __tablename__ = "database_archive_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    table_name = Column(String(64), nullable=False, index=True,
                        comment="被归档的业务表名")
    status = Column(String(16), nullable=False, default="running", index=True,
                    comment="running / completed / failed")
    dry_run = Column(Boolean, nullable=False, default=True,
                     comment="True=仅统计不删除")
    cutoff = Column(DateTime(timezone=True), nullable=False,
                    comment="保留截止时间，仅清理 created_at < cutoff 的记录")
    batch_size = Column(Integer, nullable=False, default=200)
    processed_count = Column(Integer, nullable=False, default=0,
                             comment="达到保留条件的记录数")
    deleted_count = Column(Integer, nullable=False, default=0,
                           comment="实际删除数（dry_run 时为 0）")
    error_message = Column(Text, nullable=True, comment="脱敏错误摘要")
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
