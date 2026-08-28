from pydantic import BaseModel, ConfigDict, EmailStr, Field
from datetime import datetime
from typing import Optional

# 口令 / 令牌字段统一 repr=False：模型实例的 repr 会进异常局部变量快照（Sentry 默认上传
# locals）、日志里的 %s、以及测试断言失败时打印的 call args。repr=False 只挡打印，
# 不影响赋值、校验与取值。


class UserRole:
    USER = "user"
    DEPT_ADMIN = "dept_admin"
    ADMIN = "admin"


class UserStatus:
    ACTIVE = "active"
    DISABLED = "disabled"
    LOCKED = "locked"
    PENDING = "pending"


class UserBase(BaseModel):
    username: str
    email: EmailStr
    full_name: Optional[str] = None
    organization_id: Optional[int] = None
    department_id: Optional[int] = None
    job_title: Optional[str] = None
    employee_id: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(repr=False)


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    employee_id: Optional[str] = None
    organization_id: Optional[int] = None
    department_id: Optional[int] = None


class UserLogin(BaseModel):
    username: str
    password: str = Field(repr=False)


class OAuthLoginRequest(BaseModel):
    provider: str  # wecom / dingtalk / ldap
    code: str


class LDAPLoginRequest(BaseModel):
    username: str
    password: str = Field(repr=False)


class UserOut(UserBase):
    id: int
    role: str
    status: str
    external_provider: Optional[str] = None
    last_login_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserDetailOut(UserOut):
    login_fail_count: int = 0
    locked_until: Optional[datetime] = None
    last_login_ip: Optional[str] = None
    updated_at: Optional[datetime] = None


class UserListOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    role: str
    status: str
    organization_id: Optional[int]
    department_id: Optional[int]
    job_title: Optional[str]
    last_login_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class UserRoleUpdate(BaseModel):
    role: str  # user / dept_admin / admin


class UserStatusUpdate(BaseModel):
    status: str  # active / disabled


class UserPasswordReset(BaseModel):
    new_password: str = Field(repr=False)


class LoginLogOut(BaseModel):
    id: int
    user_id: Optional[int]
    username: Optional[str]
    event_type: str
    ip_address: Optional[str]
    detail: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuditLogOut(BaseModel):
    id: int
    operator_id: int
    operator_name: str
    action: str
    target_type: Optional[str]
    target_id: Optional[int]
    target_name: Optional[str]
    detail: Optional[str]
    ip_address: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str = Field(repr=False)
    token_type: str = "bearer"
    refresh_token: Optional[str] = Field(default=None, repr=False)
    user: UserOut


class AuthorizeUrlResponse(BaseModel):
    authorize_url: str
    state: str


# ── Phase 10 Week 1 ──

class SendVerifyCodeRequest(BaseModel):
    email: EmailStr
    purpose: str = "register"  # register / reset_password


class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str
    purpose: str = "register"


class RegisterWithCodeRequest(BaseModel):
    """注册时同时带验证码，一步完成注册 + 验证"""
    username: str
    email: EmailStr
    password: str = Field(repr=False)
    code: str
    full_name: Optional[str] = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordConfirmRequest(BaseModel):
    token: str = Field(repr=False)
    new_password: str = Field(repr=False)


class WechatLoginUrlResponse(BaseModel):
    login_url: str
    state: str


class VerifyCodeSentResponse(BaseModel):
    email: str
    expires_minutes: int