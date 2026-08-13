"""连接器同步增量去重镜像：外部唯一 ID + version/hash 的 DB 级幂等。

UNIQUE(connector_id, external_id)：同一外部对象只保留一行；version_hash 变化
时更新（upsert），未变化时跳过，避免重复拉取与重复创建本地对象。
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func

from app.core.database import Base


class ConnectorSyncItem(Base):
    __tablename__ = "connector_sync_items"
    __table_args__ = (
        UniqueConstraint("connector_id", "external_id", name="uq_connector_sync_items_connector_external"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    connector_id = Column(Integer, ForeignKey("external_connectors.id"), nullable=False, index=True)
    external_id = Column(String(256), nullable=False, comment="外部系统唯一 ID")
    version_hash = Column(String(128), nullable=False, comment="sha256(external_id:version/updated_at)")
    deleted = Column(Integer, nullable=False, default=0, comment="外部对象已删除标记")
    sync_run_id = Column(Integer, nullable=True, index=True, comment="最近一次同步 run id")
    last_synced_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
