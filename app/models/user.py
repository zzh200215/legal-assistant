from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Boolean, Text
from app.core.database import Base
import enum


class WechatUser(Base):
    """微信用户绑定表"""
    __tablename__ = "wechat_users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    openid = Column(String(128), unique=True, nullable=False, index=True)
    unionid = Column(String(128), unique=True, nullable=True, index=True)
    nickname = Column(String(128), nullable=True)
    avatar_url = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EmailVerificationCode(Base):
    """邮箱验证码"""
    __tablename__ = "email_verification_codes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    email = Column(String(128), nullable=False, index=True)
    code = Column(String(8), nullable=False)
    purpose = Column(String(32), nullable=False)
    used = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PasswordResetToken(Base):
    """密码重置令牌"""
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String(128), unique=True, nullable=False, index=True)
    used = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserRole(str, enum.Enum):
    """用户角色：普通员工、部门管理员、系统管理员"""
    user = "user"  # 普通员工
    dept_admin = "dept_admin"  # 部门管理员
    admin = "admin"  # 系统管理员


class UserStatus(str, enum.Enum):
    """用户状态"""
    active = "active"  # 正常
    disabled = "disabled"  # 禁用
    locked = "locked"  # 锁定（登录失败过多）
    pending = "pending"  # 待激活
    deletion_pending = "deletion_pending"  # 注销冷却期（#95，30 天可撤销）
    deleted = "deleted"  # 已注销（主体已匿名化）


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), unique=True, nullable=False)
    hashed_password = Column(String(256), nullable=True)  # OAuth 用户可能无密码
    full_name = Column(String(128), nullable=True)

    # 角色与状态
    role = Column(String(32), default=UserRole.user.value, nullable=False)
    status = Column(String(32), default=UserStatus.active.value, nullable=False, index=True)

    # 组织归属
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    job_title = Column(String(128), nullable=True)
    employee_id = Column(String(64), nullable=True, index=True)  # 工号

    # 外部账号关联（企业微信/钉钉/LDAP）
    external_provider = Column(String(32), nullable=True, index=True)  # wecom/dingtalk/ldap
    external_user_id = Column(String(128), nullable=True, index=True)

    # 登录安全
    login_fail_count = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_login_ip = Column(String(64), nullable=True)
    password_changed_at = Column(DateTime(timezone=True), nullable=True)
    force_password_change = Column(Boolean, default=False, nullable=False)

    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 注销冷却期（#95）
    deletion_requested_at = Column(DateTime(timezone=True), nullable=True)
    deletion_confirmed_at = Column(DateTime(timezone=True), nullable=True)

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.admin.value

    @property
    def is_dept_admin(self) -> bool:
        return self.role == UserRole.dept_admin.value

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.active.value
