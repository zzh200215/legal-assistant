from pydantic import BaseModel, ConfigDict, EmailStr
from datetime import datetime
from typing import Optional


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
    password: str


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    employee_id: Optional[str] = None
    organization_id: Optional[int] = None
    department_id: Optional[int] = None


class UserLogin(BaseModel):
    username: str
    password: str


class OAuthLoginRequest(BaseModel):
    provider: str  # wecom / dingtalk / ldap
    code: str


class LDAPLoginRequest(BaseModel):
    username: str
    password: str


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
    new_password: str


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
    access_token: str
    token_type: str = "bearer"
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
    password: str
    code: str
    full_name: Optional[str] = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordConfirmRequest(BaseModel):
    token: str
    new_password: str


class WechatLoginUrlResponse(BaseModel):
    login_url: str
    state: str


class VerifyCodeSentResponse(BaseModel):
    email: str
    expires_minutes: int