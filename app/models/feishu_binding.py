"""#87/飞书绑定模型：open_id <-> user_id（M1 前置）"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from app.core.database import Base


class FeishuBinding(Base):
    __tablename__ = "feishu_bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    open_id = Column(String(128), nullable=False, unique=True, index=True)
    union_id = Column(String(128), nullable=True)
    app_id = Column(String(64), nullable=False)
    status = Column(String(16), nullable=False, server_default="active", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
