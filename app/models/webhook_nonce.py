"""Webhook nonce 去重表（P1-C）。

- ``UNIQUE(namespace, nonce)``：跨实例共享存储（数据库）保证并发重放只成功一次。
- ``expires_at`` 按 ``WEBHOOK_REPLAY_TTL_SECONDS`` 写入；过期行由写入时惰性清理，
  不阻塞请求路径。
- 只存 nonce 与来源命名空间，不存任何密钥或载荷。
"""

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint, func

from app.core.database import Base


class WebhookNonce(Base):
    __tablename__ = "webhook_nonces"
    __table_args__ = (
        UniqueConstraint("namespace", "nonce", name="uq_webhook_nonces_namespace_nonce"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    namespace = Column(String(64), nullable=False, index=True, comment="回调来源，如 feishu / stripe / signing")
    nonce = Column(String(128), nullable=False, comment="请求方 nonce 或事件唯一 ID")
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True, comment="过期时间（惰性清理）")
    created_at = Column(DateTime(timezone=True), server_default=func.now())