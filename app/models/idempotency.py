"""通用幂等键台账：数据库级唯一约束作为并发最终保障。

- UNIQUE(scope, idempotency_key)：同一业务域 + 键只允许一个生效请求。
- status：in_progress（已注册未完成，防并发重复提交）/ completed（生效并缓存响应）/
  failed（业务失败，允许同 key 重试）。
- response_snapshot：completed 时缓存响应，同 key 重放直接返回。
- expires_at：TTL 过期后由清理任务删除，允许键复用。
"""

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_keys_scope_key"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    scope = Column(String(64), nullable=False, index=True, comment="业务域，如 open_api.contract_review")
    idempotency_key = Column(String(128), nullable=False, comment="调用方幂等键")
    request_hash = Column(String(64), nullable=False, comment="规范化请求体 SHA-256")
    endpoint = Column(String(128), nullable=True, comment="动作/端点，如 POST /v1/contract-reviews")
    user_id = Column(Integer, nullable=True, index=True, comment="租户/用户作用域（服务端解析，不信任客户端）")
    organization_id = Column(Integer, nullable=True, index=True, comment="组织作用域")
    resource_id = Column(String(64), nullable=True, comment="关联资源/任务 ID（如 job id）")
    status = Column(String(16), nullable=False, default="in_progress", index=True,
                    comment="in_progress / completed / failed")
    response_snapshot = Column(Text, nullable=True, comment="completed 时缓存响应 JSON")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True,
                        comment="过期时间，清理任务删除后可复用键")
