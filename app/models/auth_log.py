"""企业登录审计日志模型"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Text
from app.core.database import Base
import enum


class LoginEventType(str, enum.Enum):
    login_success = "login_success"
    login_failed = "login_failed"
    logout = "logout"
    token_refresh = "token_refresh"
    password_change = "password_change"
    account_locked = "account_locked"
    account_unlocked = "account_unlocked"
    account_disabled = "account_disabled"
    force_logout = "force_logout"


class LoginLog(Base):
    """登录日志"""
    __tablename__ = "login_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    username = Column(String(64), nullable=True, index=True)
    event_type = Column(String(32), nullable=False, index=True)
    ip_address = Column(String(64), nullable=True)
    user_agent = Column(String(512), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    archived_at = Column(DateTime(timezone=True), nullable=True, index=True,
                         comment="归档时间（保留任务归档后标记，默认不物理删除）")


class AdminAuditLog(Base):
    """管理员操作审计日志"""
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    operator_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    operator_name = Column(String(64), nullable=False)
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(32), nullable=True, index=True)  # user/department/organization/document
    target_id = Column(Integer, nullable=True, index=True)
    target_name = Column(String(128), nullable=True)
    detail = Column(Text, nullable=True)
    ip_address = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    archived_at = Column(DateTime(timezone=True), nullable=True, index=True,
                         comment="归档时间（保留任务归档后标记，默认不物理删除）")