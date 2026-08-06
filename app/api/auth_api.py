"""认证 API：支持本地登录、企业微信、钉钉、LDAP"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from typing import Optional
import secrets

from app.core.database import get_db
from app.core.auth import hash_password, verify_password, create_access_token, get_current_user
from app.core.api_response import api_error
from app.core.config import get_settings
from app.models.user import User, UserRole, UserStatus
from app.models.auth_log import LoginLog, AdminAuditLog
from app.services.enterprise_auth_service import enterprise_auth_service
from app.services.audit_log_service import audit_log_service, AuditAction
from app.services.user_auth_service import user_auth_service
from app.schemas.user import (
    UserCreate, UserLogin, UserOut, UserDetailOut, UserListOut,
    UserRoleUpdate, UserStatusUpdate, UserPasswordReset,
    TokenResponse, AuthorizeUrlResponse, OAuthLoginRequest,
    LoginLogOut, AuditLogOut,
    SendVerifyCodeRequest, VerifyEmailRequest, RegisterWithCodeRequest,
    ForgotPasswordRequest, ResetPasswordConfirmRequest,
    WechatLoginUrlResponse, VerifyCodeSentResponse,
)

router = APIRouter()
settings = get_settings()


def get_client_ip(request: Request) -> Optional[str]:
    """获取客户端 IP"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


# ================== 登录相关 ==================

@router.post("/login", response_model=TokenResponse)
def login(req: UserLogin, request: Request, db: Session = Depends(get_db)):
    """本地密码登录"""
    from app.services.security_audit_service import write_event
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent")

    user, token = enterprise_auth_service.local_login(
        db=db, username=req.username, password=req.password,
        ip_address=ip, user_agent=ua
    )

    if not user or not token:
        write_event(
            event_type="login", actor_type="user",
            actor_id=req.username[:32], result="failure",
            db=db,
        )
        raise api_error(401, "用户名或密码错误，或账号已被锁定/禁用", code="INVALID_CREDENTIALS")

    write_event(
        event_type="login", actor_type="user",
        actor_id=str(user.id), result="success",
        organization_id=getattr(user, "organization_id", None),
        db=db,
    )
    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user)
    )


@router.post("/oauth/authorize-url", response_model=AuthorizeUrlResponse)
def get_oauth_authorize_url(
    provider: str = Query(..., description="wecom / dingtalk"),
    redirect_uri: str = Query(...),
    db: Session = Depends(get_db),
):
    """获取 OAuth 授权 URL"""
    prov = enterprise_auth_service.get_provider(provider)
    if not prov:
        raise api_error(400, f"不支持的登录方式: {provider}", code="UNSUPPORTED_PROVIDER")

    state = secrets.token_urlsafe(16)
    authorize_url = prov.get_authorize_url(redirect_uri, state)

    return AuthorizeUrlResponse(authorize_url=authorize_url, state=state)


@router.post("/oauth/callback", response_model=TokenResponse)
def oauth_callback(
    req: OAuthLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """OAuth 回调登录"""
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent")

    user, token = enterprise_auth_service.oauth_login(
        db=db, provider_name=req.provider, code=req.code,
        ip_address=ip, user_agent=ua
    )

    if not user or not token:
        raise api_error(401, "OAuth 登录失败", code="OAUTH_LOGIN_FAILED")

    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user)
    )


@router.post("/ldap/login", response_model=TokenResponse)
def ldap_login(
    req: UserLogin,
    request: Request,
    db: Session = Depends(get_db),
):
    """LDAP 登录"""
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent")

    user, token = enterprise_auth_service.ldap_login(
        db=db, username=req.username, password=req.password,
        ip_address=ip, user_agent=ua
    )

    if not user or not token:
        raise api_error(401, "LDAP 认证失败", code="LDAP_AUTH_FAILED")

    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user)
    )


@router.post("/logout")
def logout(current_user: User = Depends(get_current_user)):
    """登出（客户端清除 Token 即可）"""
    return {"message": "已登出"}


@router.post("/register", response_model=TokenResponse)
def register(req: UserCreate, db: Session = Depends(get_db)):
    """注册（仅用于本地开发/测试，生产环境建议禁用）"""
    if db.query(User).filter(User.username == req.username).first():
        raise api_error(409, "用户名已存在", code="USERNAME_EXISTS")

    if db.query(User).filter(User.email == req.email).first():
        raise api_error(409, "邮箱已注册", code="EMAIL_EXISTS")

    user = User(
        username=req.username,
        email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        role=UserRole.user.value,
        status=UserStatus.active.value,
        # 自注册不接受 organization_id/department_id，防止伪造归属读取组织级数据；
        # 组织归属须经管理员/组织邀请流程分配
        job_title=req.job_title,
        employee_id=req.employee_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": user.id, "role": user.role})
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


# ================== 当前用户 ==================

@router.get("/me", response_model=UserDetailOut)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return UserDetailOut.model_validate(current_user)


# ================== 用户管理（管理员） ==================

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求管理员权限"""
    if current_user.role not in (UserRole.admin.value, UserRole.dept_admin.value):
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")
    return current_user


def require_system_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求系统管理员权限"""
    if current_user.role != UserRole.admin.value:
        raise api_error(403, "需要系统管理员权限", code="SYSTEM_ADMIN_REQUIRED")
    return current_user


@router.get("/users", response_model=list[UserListOut])
def list_users(
    status: Optional[str] = Query(None),  # noqa: F811 - shadows fastapi.status; param name is API contract
    role: Optional[str] = Query(None),
    department_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """查询用户列表"""
    query = db.query(User)

    # 部门管理员只能查看本部门用户
    if admin.role == UserRole.dept_admin.value:
        query = query.filter(User.department_id == admin.department_id)

    if status:
        query = query.filter(User.status == status)

    if role:
        query = query.filter(User.role == role)

    if department_id:
        query = query.filter(User.department_id == department_id)

    if keyword:
        query = query.filter(
            (User.username.contains(keyword)) |
            (User.email.contains(keyword)) |
            (User.full_name.contains(keyword))
        )

    users = query.order_by(User.created_at.desc()).limit(100).all()
    return [UserListOut.model_validate(u) for u in users]


@router.get("/users/{user_id}", response_model=UserDetailOut)
def get_user_detail(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """获取用户详情"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise api_error(404, "用户不存在", code="USER_NOT_FOUND")

    # 部门管理员只能查看本部门用户
    if admin.role == UserRole.dept_admin.value and user.department_id != admin.department_id:
        raise api_error(403, "无权查看该用户", code="PERMISSION_DENIED")

    return UserDetailOut.model_validate(user)


@router.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    req: UserRoleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_system_admin),
):
    """更新用户角色（仅系统管理员）"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise api_error(404, "用户不存在", code="USER_NOT_FOUND")

    old_role = user.role
    user.role = req.role
    db.add(user)
    db.commit()

    audit_log_service.log_user_action(
        db=db, operator=admin, action=AuditAction.USER_ROLE_CHANGE,
        target_user=user,
        detail=f"角色从 {old_role} 变更为 {req.role}",
        ip_address=get_client_ip(request)
    )

    return {"id": user.id, "role": user.role}


@router.put("/users/{user_id}/status")
def update_user_status(
    user_id: int,
    req: UserStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新用户状态"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise api_error(404, "用户不存在", code="USER_NOT_FOUND")

    # 部门管理员只能操作本部门用户
    if admin.role == UserRole.dept_admin.value and user.department_id != admin.department_id:
        raise api_error(403, "无权操作该用户", code="PERMISSION_DENIED")

    # 不能操作自己
    if user.id == admin.id:
        raise api_error(400, "不能修改自己的状态", code="CANNOT_MODIFY_SELF")

    old_status = user.status
    user.status = req.status
    db.add(user)
    db.commit()

    action = AuditAction.USER_DISABLE if req.status == UserStatus.DISABLED.value else AuditAction.USER_ENABLE
    audit_log_service.log_user_action(
        db=db, operator=admin, action=action,
        target_user=user,
        detail=f"状态从 {old_status} 变更为 {req.status}",
        ip_address=get_client_ip(request)
    )

    return {"id": user.id, "status": user.status}


@router.post("/users/{user_id}/unlock")
def unlock_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """解锁用户"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise api_error(404, "用户不存在", code="USER_NOT_FOUND")

    if admin.role == UserRole.dept_admin.value and user.department_id != admin.department_id:
        raise api_error(403, "无权操作该用户", code="PERMISSION_DENIED")

    enterprise_auth_service.unlock_user(db, user_id, admin.id)

    audit_log_service.log_user_action(
        db=db, operator=admin, action=AuditAction.USER_UNLOCK,
        target_user=user,
        ip_address=get_client_ip(request)
    )

    return {"id": user.id, "status": user.status}


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    req: UserPasswordReset,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """重置用户密码"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise api_error(404, "用户不存在", code="USER_NOT_FOUND")

    if admin.role == UserRole.dept_admin.value and user.department_id != admin.department_id:
        raise api_error(403, "无权操作该用户", code="PERMISSION_DENIED")

    user.hashed_password = hash_password(req.new_password)
    user.force_password_change = True
    db.add(user)
    db.commit()

    audit_log_service.log_user_action(
        db=db, operator=admin, action=AuditAction.USER_PASSWORD_RESET,
        target_user=user,
        ip_address=get_client_ip(request)
    )

    return {"id": user.id, "message": "密码已重置"}


@router.post("/users/{user_id}/force-logout")
def force_logout_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """强制用户下线"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise api_error(404, "用户不存在", code="USER_NOT_FOUND")

    if admin.role == UserRole.dept_admin.value and user.department_id != admin.department_id:
        raise api_error(403, "无权操作该用户", code="PERMISSION_DENIED")

    enterprise_auth_service.force_logout(db, user_id, admin.id)

    audit_log_service.log_user_action(
        db=db, operator=admin, action=AuditAction.USER_FORCE_LOGOUT,
        target_user=user,
        ip_address=get_client_ip(request)
    )

    return {"id": user.id, "message": "已强制下线"}


# ================== 登录日志 ==================

@router.get("/login-logs", response_model=list[LoginLogOut])
def list_login_logs(
    user_id: Optional[int] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """查询登录日志"""
    query = db.query(LoginLog)

    if user_id:
        query = query.filter(LoginLog.user_id == user_id)

    if event_type:
        query = query.filter(LoginLog.event_type == event_type)

    logs = query.order_by(LoginLog.created_at.desc()).limit(limit).all()
    return [LoginLogOut.model_validate(log) for log in logs]


# ================== 审计日志 ==================

@router.get("/audit-logs", response_model=list[AuditLogOut])
def list_audit_logs(
    operator_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    target_type: Optional[str] = Query(None),
    target_id: Optional[int] = Query(None),
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    admin: User = Depends(require_system_admin),
):
    """查询审计日志（仅系统管理员）"""
    logs = audit_log_service.list_logs(
        db=db,
        operator_id=operator_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        limit=limit,
    )
    return [AuditLogOut.model_validate(log) for log in logs]


# ================== Phase 10 Week 1: 邮箱验证 ==================

@router.post("/send-verification-code", response_model=VerifyCodeSentResponse)
def send_verification_code(req: SendVerifyCodeRequest, db: Session = Depends(get_db)):
    """发送邮箱验证码"""
    if req.purpose == "register":
        # 注册时检查邮箱是否已存在
        if db.query(User).filter(User.email == req.email).first():
            raise api_error(409, "邮箱已注册", code="EMAIL_EXISTS")

    user_auth_service.send_verification_code(db, req.email, req.purpose)
    return VerifyCodeSentResponse(
        email=req.email,
        expires_minutes=settings.EMAIL_VERIFY_CODE_EXPIRE_MINUTES
    )


@router.post("/verify-email")
def verify_email(req: VerifyEmailRequest, db: Session = Depends(get_db)):
    """验证邮箱验证码"""
    if not user_auth_service.verify_email_code(db, req.email, req.code, req.purpose):
        raise api_error(400, "验证码无效或已过期", code="INVALID_CODE")
    return {"message": "验证成功"}


@router.post("/register-with-code", response_model=TokenResponse)
def register_with_code(req: RegisterWithCodeRequest, db: Session = Depends(get_db)):
    """注册（带邮箱验证码）"""
    if db.query(User).filter(User.username == req.username).first():
        raise api_error(409, "用户名已存在", code="USERNAME_EXISTS")

    if db.query(User).filter(User.email == req.email).first():
        raise api_error(409, "邮箱已注册", code="EMAIL_EXISTS")

    # 验证邮箱验证码
    if not user_auth_service.verify_email_code(db, req.email, req.code, "register"):
        raise api_error(400, "验证码无效或已过期", code="INVALID_CODE")

    # 创建用户（已激活）
    user = User(
        username=req.username,
        email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        role=UserRole.user.value,
        status=UserStatus.active.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": user.id, "role": user.role})
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


# ================== Phase 10 Week 1: 密码找回 ==================

@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """请求密码重置（发送重置链接到邮箱）"""
    reset_token = user_auth_service.request_password_reset(db, req.email)
    if not reset_token:
        # 安全考虑：即使用户不存在也返回成功，避免邮箱枚举
        pass
    return {"message": "如果该邮箱已注册，您将收到密码重置邮件"}


@router.post("/reset-password", response_model=TokenResponse)
def reset_password(req: ResetPasswordConfirmRequest, db: Session = Depends(get_db)):
    """凭token重置密码"""
    user = user_auth_service.confirm_password_reset(db, req.token, req.new_password)
    if not user:
        raise api_error(400, "重置链接无效或已过期", code="INVALID_RESET_TOKEN")

    token = create_access_token({"sub": user.id, "role": user.role})
    return TokenResponse(access_token=token, user=UserOut.model_validate(user))


# ================== Phase 10 Week 1: 微信扫码登录 ==================

@router.get("/wechat/login-url", response_model=WechatLoginUrlResponse)
def get_wechat_login_url():
    """获取微信扫码登录URL"""
    state = secrets.token_urlsafe(16)
    login_url = user_auth_service.get_wechat_login_url(state)
    if not login_url:
        raise api_error(500, "微信登录未配置", code="WECHAT_NOT_CONFIGURED")
    return WechatLoginUrlResponse(login_url=login_url, state=state)


@router.get("/wechat/callback", response_model=TokenResponse)
def wechat_callback(
    code: str = Query(...),
    state: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """微信回调登录"""
    ip = get_client_ip(request)
    user, token = user_auth_service.wechat_callback(db, code, ip)

    if not user or not token:
        raise api_error(401, "微信登录失败", code="WECHAT_LOGIN_FAILED")

    return TokenResponse(
        access_token=token,
        user=UserOut.model_validate(user)
    )
