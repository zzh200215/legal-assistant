"""认证 API：支持本地登录、企业微信、钉钉、LDAP、邮箱验证、微信、MFA、refresh token"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
import secrets

from app.core.database import get_db
from app.core.auth import hash_password, verify_password, create_access_token, get_current_user
from app.core.api_response import api_error
from app.core.config import get_settings
from app.models.user import User, UserRole, UserStatus
from app.models.auth_log import LoginLog, AdminAuditLog
from app.services.auth.enterprise_auth_service import enterprise_auth_service
from app.services.observability.audit_log_service import audit_log_service, AuditAction
from app.services.auth.user_auth_service import user_auth_service
from app.services.auth.auth_token_service import auth_token_service, get_client_ip
from app.services.auth.mfa_service import mfa_service
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


def _device_headers(request: Request | None) -> tuple[Optional[str], Optional[str]]:
    """提取设备标识与 User-Agent。device_id 由客户端提供或确定性派生。"""
    ua = request.headers.get("User-Agent") if request is not None else None
    device_id = request.headers.get("X-Device-Id") if request is not None else None
    if not device_id:
        device_id = auth_token_service.default_device_id(ua, get_client_ip(request))
    return device_id, ua


def _issue_login_response(
    db: Session,
    user: User,
    request: Request,
    *,
    ip: Optional[str],
    ua: Optional[str],
    login_failures: int = 0,
):
    """登录成功后的统一响应：已启用 MFA 则要求第二步，否则签发 token 会话。

    风险策略（确定性）：
    - 已启用 MFA 的用户：一律要求 MFA challenge（响应携带风险等级供前端展示）。
    - 未启用 MFA 的用户：高危（禁用/锁定/连续失败≥3）拒绝登录，中危仅记录设备。
    """
    device_id, _ = _device_headers(request)
    risk = auth_token_service.assess_risk(
        db, user, device_id=device_id, ip_address=ip, user_agent=ua,
        login_failures=login_failures,
    )
    # 记录设备，使同一设备后续登录降为已知设备（低危）。
    auth_token_service.record_device(
        db, user, device_id=device_id, ip_address=ip, user_agent=ua, risk=risk,
    )

    if mfa_service.mfa_enabled(db, user.id):
        challenge, ttl = mfa_service.create_challenge(db, user.id)
        return {
            "mfa_required": True,
            "challenge": challenge,
            "expires_minutes": ttl,
            "risk_level": risk.risk_level,
            "risk_reasons": list(risk.reasons),
            "user": UserOut.model_validate(user),
        }

    if risk.risk_level == "high":
        raise api_error(401, "登录被安全策略拒绝", code="LOGIN_RISK_BLOCKED")

    session = auth_token_service.issue_session(
        db, user, device_id=device_id, ip_address=ip, user_agent=ua,
        login_failures=login_failures, risk=risk,
    )
    return TokenResponse(
        access_token=session["access_token"],
        refresh_token=session["refresh_token"],
        user=UserOut.model_validate(user),
    )


def _issue_token_response(db: Session, user: User) -> TokenResponse:
    """注册/密码重置等场景签发会话（无请求上下文时使用默认设备标识）。"""
    session = auth_token_service.issue_session(db, user)
    return TokenResponse(
        access_token=session["access_token"],
        refresh_token=session["refresh_token"],
        user=UserOut.model_validate(user),
    )


# ================== 登录相关 ==================

@router.post("/login")
def login(req: UserLogin, request: Request, db: Session = Depends(get_db)):
    """本地密码登录"""
    from app.services.org.security_audit_service import write_event
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
    return _issue_login_response(db, user, request, ip=ip, ua=ua)


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


@router.post("/oauth/callback")
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

    return _issue_login_response(db, user, request, ip=ip, ua=ua)


@router.post("/ldap/login")
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

    return _issue_login_response(db, user, request, ip=ip, ua=ua)


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


@router.post("/refresh")
def refresh_token(req: RefreshTokenRequest, request: Request, db: Session = Depends(get_db)):
    """refresh token 单次轮换；同一 token 重复使用判定为重放并撤销整个 family。"""
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent")
    device_id, _ = _device_headers(request)
    result = auth_token_service.rotate_refresh_token(
        db, req.refresh_token, device_id=device_id, ip_address=ip, user_agent=ua
    )
    if not result:
        raise api_error(401, "刷新令牌无效或已被重放", code="INVALID_REFRESH_TOKEN")
    return {
        "access_token": result["access_token"],
        "refresh_token": result["refresh_token"],
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(req: Optional[LogoutRequest] = None, request: Request = None,
           db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """登出：撤销当前 access token，可选撤销 refresh token。"""
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        auth_token_service.revoke_access_token(auth_header[7:], db, reason="logout")
    if req and req.refresh_token:
        auth_token_service.revoke_refresh_token(db, req.refresh_token, reason="logout")
    return {"message": "已登出"}


@router.post("/logout-all")
def logout_all(request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """全部设备退出：递增 token 版本使所有 access token 失效，并撤销全部 refresh token。"""
    auth_token_service.increment_token_version(db, current_user)
    auth_token_service.revoke_all_devices(db, current_user)
    return {"message": "已从全部设备登出"}


@router.post("/register")
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

    return _issue_token_response(db, user)


# ================== 当前用户 ==================

@router.get("/me", response_model=UserDetailOut)
def get_current_user_info(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return UserDetailOut.model_validate(current_user)


@router.get("/security-status")
def get_security_status(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户安全状态：MFA、设备、最近风险。"""
    from app.models.security_auth import AuthDevice, RefreshToken

    mfa_enabled = mfa_service.mfa_enabled(db, current_user.id)
    devices = (
        db.query(AuthDevice)
        .filter(AuthDevice.user_id == current_user.id)
        .order_by(AuthDevice.last_seen_at.desc())
        .limit(20)
        .all()
    )
    active_sessions = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .count()
    )
    return {
        "mfa_enabled": mfa_enabled,
        "token_version": current_user.token_version,
        "active_devices": [
            {
                "device_id": d.device_id,
                "risk_level": d.risk_level,
                "risk_reason": d.risk_reason,
                "last_seen_at": d.last_seen_at,
            }
            for d in devices
        ],
        "active_sessions": active_sessions,
    }


# ================== MFA ==================

@router.post("/mfa/setup")
def mfa_setup(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """开始启用 MFA：生成 TOTP secret（confirm 前不生效）。"""
    try:
        _, otpauth = mfa_service.setup(db, current_user.id)
    except ValueError as exc:
        raise api_error(400, str(exc), code="MFA_ALREADY_ENABLED")
    return {"otpauth_uri": otpauth}


@router.post("/mfa/confirm")
def mfa_confirm(
    code: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """用一次性验证码确认启用 MFA，返回恢复码（仅展示一次）。"""
    if not mfa_service.confirm(db, current_user.id, code):
        raise api_error(400, "验证码无效", code="MFA_INVALID_CODE")
    recovery_codes = mfa_service.generate_recovery_codes(db, current_user.id)
    return {"enabled": True, "recovery_codes": recovery_codes}


@router.post("/mfa/disable")
def mfa_disable(
    code: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """禁用 MFA（需通过一次验证码校验）。"""
    if not mfa_service.verify(db, current_user.id, code):
        raise api_error(400, "验证码无效", code="MFA_INVALID_CODE")
    mfa_service.disable(db, current_user.id)
    return {"enabled": False}


@router.post("/mfa/recovery-codes")
def mfa_regenerate_recovery_codes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重新生成恢复码（旧恢复码全部作废）。"""
    if not mfa_service.mfa_enabled(db, current_user.id):
        raise api_error(400, "尚未启用 MFA", code="MFA_NOT_ENABLED")
    recovery_codes = mfa_service.generate_recovery_codes(db, current_user.id)
    return {"recovery_codes": recovery_codes}


@router.post("/mfa/verify")
def mfa_verify_login(
    challenge: str = Query(...),
    code: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """登录第二步：校验 challenge + MFA 验证码，成功后签发 token 会话。"""
    from app.models.security_auth import MFAChallenge

    row = db.query(MFAChallenge).filter(MFAChallenge.challenge_jti == challenge).first()
    if not row or row.purpose != "mfa_login":
        raise api_error(401, "登录会话已失效", code="MFA_CHALLENGE_INVALID")
    user = db.query(User).filter(User.id == row.user_id).first()
    if not user or user.status != UserStatus.active.value:
        raise api_error(401, "账号不可用", code="INVALID_CREDENTIALS")
    if not mfa_service.validate_challenge(db, challenge, user.id):
        raise api_error(401, "登录会话已失效", code="MFA_CHALLENGE_INVALID")
    if not mfa_service.verify(db, user.id, code):
        raise api_error(400, "MFA 验证码无效", code="MFA_INVALID_CODE")
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent") if request else None
    device_id, _ = _device_headers(request)
    session = auth_token_service.issue_session(
        db, user, device_id=device_id, ip_address=ip, user_agent=ua,
    )
    return TokenResponse(
        access_token=session["access_token"],
        refresh_token=session["refresh_token"],
        user=UserOut.model_validate(user),
    )


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

    # 禁用/锁定后递增 token 版本，使该用户已有 token 立即失效。
    if req.status in (UserStatus.disabled.value, UserStatus.locked.value):
        auth_token_service.increment_token_version(db, user)

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
    # 密码重置后使该用户所有旧 token 失效。
    auth_token_service.increment_token_version(db, user)

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
    """强制用户下线：递增 token 版本，使该用户所有 token 立即失效。"""
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


@router.post("/register-with-code")
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

    return _issue_token_response(db, user)


# ================== Phase 10 Week 1: 密码找回 ==================

@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """请求密码重置（发送重置链接到邮箱）"""
    reset_token = user_auth_service.request_password_reset(db, req.email)
    if not reset_token:
        # 安全考虑：即使用户不存在也返回成功，避免邮箱枚举
        pass
    return {"message": "如果该邮箱已注册，您将收到密码重置邮件"}


@router.post("/reset-password")
def reset_password(req: ResetPasswordConfirmRequest, db: Session = Depends(get_db)):
    """凭token重置密码"""
    user = user_auth_service.confirm_password_reset(db, req.token, req.new_password)
    if not user:
        raise api_error(400, "重置链接无效或已过期", code="INVALID_RESET_TOKEN")

    # 密码重置后使该用户所有旧 token 失效。
    auth_token_service.increment_token_version(db, user)

    return _issue_token_response(db, user)


# ================== Phase 10 Week 1: 微信扫码登录 ==================

@router.get("/wechat/login-url", response_model=WechatLoginUrlResponse)
def get_wechat_login_url():
    """获取微信扫码登录URL"""
    state = secrets.token_urlsafe(16)
    login_url = user_auth_service.get_wechat_login_url(state)
    if not login_url:
        raise api_error(500, "微信登录未配置", code="WECHAT_NOT_CONFIGURED")
    return WechatLoginUrlResponse(login_url=login_url, state=state)


@router.get("/wechat/callback")
def wechat_callback(
    code: str = Query(...),
    state: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """微信回调登录"""
    ip = get_client_ip(request)
    ua = request.headers.get("User-Agent") if request else None
    user, token = user_auth_service.wechat_callback(db, code, ip)

    if not user or not token:
        raise api_error(401, "微信登录失败", code="WECHAT_LOGIN_FAILED")

    return _issue_login_response(db, user, request, ip=ip, ua=ua)
