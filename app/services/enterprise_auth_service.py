"""企业统一登录服务：支持企业微信、钉钉、LDAP"""
from datetime import datetime, timezone, timedelta
from typing import Optional
import hashlib
import secrets

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.user import User, UserRole, UserStatus
from app.models.auth_log import LoginLog, LoginEventType
from app.models.org import Department, Organization
from app.core.auth import hash_password, create_access_token
from app.core.config import get_settings


settings = get_settings()


class EnterpriseAuthProvider:
    """企业认证提供商基类"""
    provider_name: str = ""

    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        raise NotImplementedError

    def get_user_info(self, code: str) -> dict:
        """获取用户信息，返回格式：{
            'external_user_id': str,
            'username': str,
            'email': str,
            'full_name': str,
            'employee_id': str,
            'department_code': str,
            'organization_code': str,
        }"""
        raise NotImplementedError

    def sync_organization(self, db: Session) -> list[Organization]:
        """同步组织架构"""
        raise NotImplementedError

    def sync_department(self, db: Session, org_code: str) -> list[Department]:
        """同步部门"""
        raise NotImplementedError


class WeComAuthProvider(EnterpriseAuthProvider):
    """企业微信认证"""
    provider_name = "wecom"

    def __init__(self):
        self.corp_id = settings.WECOM_CORP_ID or ""
        self.agent_id = settings.WECOM_AGENT_ID or ""
        self.secret = settings.WECOM_SECRET or ""
        self._access_token_cache: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        # 企业微信 OAuth2 授权 URL
        return (
            f"https://open.weixin.qq.com/connect/oauth2/authorize"
            f"?appid={self.corp_id}&redirect_uri={redirect_uri}"
            f"&response_type=code&scope=snsapi_base&state={state}#wechat_redirect"
        )

    def _get_access_token(self) -> str:
        """获取企业微信 access_token"""
        if self._access_token_cache and self._token_expires_at:
            if datetime.now(timezone.utc) < self._token_expires_at:
                return self._access_token_cache

        # 实际实现需要调用企业微信 API
        # https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={secret}
        # 这里返回占位，实际部署时需配置
        return ""

    def get_user_info(self, code: str) -> dict:
        """通过 code 获取用户信息"""
        # 实际实现：
        # 1. 用 code 换取用户身份信息
        # 2. 获取用户详情
        # 这里返回模拟数据结构
        return {
            "external_user_id": "",
            "username": "",
            "email": "",
            "full_name": "",
            "employee_id": "",
            "department_code": "",
            "organization_code": "",
        }


class DingTalkAuthProvider(EnterpriseAuthProvider):
    """钉钉认证"""
    provider_name = "dingtalk"

    def __init__(self):
        self.app_key = settings.DINGTALK_APP_KEY or ""
        self.app_secret = settings.DINGTALK_APP_SECRET or ""
        self._access_token_cache: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        return (
            f"https://login.dingtalk.com/oauth2/auth"
            f"?redirect_uri={redirect_uri}&client_id={self.app_key}"
            f"&response_type=code&scope=openid&state={state}&prompt=consent"
        )

    def _get_access_token(self) -> str:
        """获取钉钉 access_token"""
        # 实际实现需要调用钉钉 API
        return ""

    def get_user_info(self, code: str) -> dict:
        """通过 code 获取用户信息"""
        # 实际实现：
        # 1. 用 code 换取 access_token 和 unionId
        # 2. 获取用户详情
        return {
            "external_user_id": "",
            "username": "",
            "email": "",
            "full_name": "",
            "employee_id": "",
            "department_code": "",
            "organization_code": "",
        }


class LDAPAuthProvider(EnterpriseAuthProvider):
    """LDAP 认证"""
    provider_name = "ldap"

    def __init__(self):
        self.ldap_url = settings.LDAP_URL or ""
        self.ldap_base_dn = settings.LDAP_BASE_DN or ""
        self.ldap_bind_dn = settings.LDAP_BIND_DN or ""
        self.ldap_bind_password = settings.LDAP_BIND_PASSWORD or ""

    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        # LDAP 不需要跳转，直接表单登录
        return ""

    def verify_credentials(self, username: str, password: str) -> Optional[dict]:
        """验证 LDAP 用户凭据"""
        # 实际实现需要 ldap3 库
        # 1. Bind 到 LDAP
        # 2. 搜索用户
        # 3. 验证密码
        # 4. 获取用户属性
        return None

    def get_user_info(self, code: str) -> dict:
        # LDAP 不使用 code 模式
        return {}


class EnterpriseAuthService:
    """企业统一登录服务"""

    MAX_LOGIN_FAIL_COUNT = 5
    LOCK_DURATION_MINUTES = 30

    def __init__(self):
        self.providers: dict[str, EnterpriseAuthProvider] = {}
        if settings.WECOM_CORP_ID:
            self.providers["wecom"] = WeComAuthProvider()
        if settings.DINGTALK_APP_KEY:
            self.providers["dingtalk"] = DingTalkAuthProvider()
        if settings.LDAP_URL:
            self.providers["ldap"] = LDAPAuthProvider()

    def get_provider(self, provider_name: str) -> Optional[EnterpriseAuthProvider]:
        return self.providers.get(provider_name)

    def oauth_login(
        self,
        db: Session,
        provider_name: str,
        code: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[Optional[User], Optional[str]]:
        """OAuth 登录"""
        provider = self.get_provider(provider_name)
        if not provider:
            return None, None

        user_info = provider.get_user_info(code)
        external_user_id = user_info.get("external_user_id")
        if not external_user_id:
            return None, None

        # 查找或创建用户
        user = db.query(User).filter(
            and_(
                User.external_provider == provider_name,
                User.external_user_id == external_user_id,
            )
        ).first()

        if not user:
            # 自动创建用户
            user = self._create_user_from_oauth(db, provider_name, user_info)

        if not user or not user.is_active:
            return None, None

        # 记录登录日志
        self._record_login_event(
            db, user.id, user.username, LoginEventType.login_success,
            ip_address, user_agent, f"OAuth login via {provider_name}"
        )

        # 更新登录信息
        user.login_fail_count = 0
        user.last_login_at = datetime.now(timezone.utc)
        user.last_login_ip = ip_address
        db.add(user)
        db.commit()

        token = create_access_token({"sub": user.id, "role": user.role})
        return user, token

    def ldap_login(
        self,
        db: Session,
        username: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[Optional[User], Optional[str]]:
        """LDAP 登录"""
        provider = self.get_provider("ldap")
        if not provider:
            return None, None

        ldap_user_info = provider.verify_credentials(username, password)
        if not ldap_user_info:
            # 记录失败
            user = db.query(User).filter(User.username == username).first()
            if user:
                self._handle_login_failure(db, user, ip_address, user_agent)
            return None, None

        external_user_id = ldap_user_info.get("external_user_id", username)

        user = db.query(User).filter(
            and_(
                User.external_provider == "ldap",
                User.external_user_id == external_user_id,
            )
        ).first()

        if not user:
            user = self._create_user_from_oauth(db, "ldap", ldap_user_info)

        if not user or not user.is_active:
            return None, None

        self._record_login_event(
            db, user.id, user.username, LoginEventType.login_success,
            ip_address, user_agent, "LDAP login"
        )

        user.login_fail_count = 0
        user.last_login_at = datetime.now(timezone.utc)
        user.last_login_ip = ip_address
        db.add(user)
        db.commit()

        token = create_access_token({"sub": user.id, "role": user.role})
        return user, token

    def local_login(
        self,
        db: Session,
        username: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> tuple[Optional[User], Optional[str]]:
        """本地密码登录"""
        from app.core.auth import verify_password

        user = db.query(User).filter(User.username == username).first()

        if not user:
            self._record_login_event(
                db, None, username, LoginEventType.login_failed,
                ip_address, user_agent, "User not found"
            )
            return None, None

        # 检查是否锁定
        if user.status == UserStatus.locked.value:
            locked_until = user.locked_until
            if locked_until and locked_until.tzinfo is None:
                # SQLite/MySQL 读回无 tzinfo，统一按 UTC 处理
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            if locked_until and datetime.now(timezone.utc) < locked_until:
                self._record_login_event(
                    db, user.id, user.username, LoginEventType.login_failed,
                    ip_address, user_agent, "Account locked"
                )
                return None, None
            else:
                # 锁定已过期，解锁
                user.status = UserStatus.active.value
                user.locked_until = None
                user.login_fail_count = 0

        # 检查状态
        if user.status == UserStatus.disabled.value:
            self._record_login_event(
                db, user.id, user.username, LoginEventType.login_failed,
                ip_address, user_agent, "Account disabled"
            )
            return None, None

        # 验证密码
        if not user.hashed_password or not verify_password(password, user.hashed_password):
            self._handle_login_failure(db, user, ip_address, user_agent)
            return None, None

        # 登录成功
        self._record_login_event(
            db, user.id, user.username, LoginEventType.login_success,
            ip_address, user_agent, "Local password login"
        )

        user.login_fail_count = 0
        user.last_login_at = datetime.now(timezone.utc)
        user.last_login_ip = ip_address
        db.add(user)
        db.commit()

        token = create_access_token({"sub": user.id, "role": user.role})
        return user, token

    def _handle_login_failure(self, db: Session, user: User, ip: Optional[str], ua: Optional[str]):
        """处理登录失败"""
        user.login_fail_count += 1

        detail = f"Login failed, count: {user.login_fail_count}"
        if user.login_fail_count >= self.MAX_LOGIN_FAIL_COUNT:
            user.status = UserStatus.locked.value
            user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=self.LOCK_DURATION_MINUTES)
            detail = f"Account locked until {user.locked_until}"
            self._record_login_event(db, user.id, user.username, LoginEventType.account_locked, ip, ua, detail)
        else:
            self._record_login_event(db, user.id, user.username, LoginEventType.login_failed, ip, ua, detail)

        db.add(user)
        db.commit()

    def _create_user_from_oauth(self, db: Session, provider: str, user_info: dict) -> User:
        """从 OAuth 信息创建用户"""
        username = user_info.get("username") or user_info.get("external_user_id")
        email = user_info.get("email") or f"{username}@placeholder.local"

        # 查找或创建组织
        org_code = user_info.get("organization_code")
        organization = None
        if org_code:
            organization = db.query(Organization).filter(Organization.code == org_code).first()

        # 查找或创建部门
        dept_code = user_info.get("department_code")
        department = None
        if dept_code and organization:
            department = db.query(Department).filter(
                and_(
                    Department.organization_id == organization.id,
                    Department.code == dept_code,
                )
            ).first()

        user = User(
            username=username,
            email=email,
            hashed_password=None,  # OAuth 用户无密码
            full_name=user_info.get("full_name"),
            role=UserRole.user.value,
            status=UserStatus.active.value,
            external_provider=provider,
            external_user_id=user_info.get("external_user_id"),
            employee_id=user_info.get("employee_id"),
            organization_id=organization.id if organization else None,
            department_id=department.id if department else None,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def _record_login_event(
        self,
        db: Session,
        user_id: Optional[int],
        username: Optional[str],
        event_type: LoginEventType,
        ip_address: Optional[str],
        user_agent: Optional[str],
        detail: Optional[str],
    ):
        """记录登录事件"""
        log = LoginLog(
            user_id=user_id,
            username=username,
            event_type=event_type.value,
            ip_address=ip_address,
            user_agent=user_agent,
            detail=detail,
        )
        db.add(log)
        db.commit()

    def unlock_user(self, db: Session, user_id: int, operator_id: int) -> bool:
        """解锁用户"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return False

        user.status = UserStatus.active.value
        user.locked_until = None
        user.login_fail_count = 0
        db.add(user)
        db.commit()

        self._record_login_event(
            db, user.id, user.username, LoginEventType.account_unlocked,
            None, None, f"Unlocked by admin {operator_id}"
        )
        return True

    def disable_user(self, db: Session, user_id: int, operator_id: int) -> bool:
        """禁用用户"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return False

        user.status = UserStatus.disabled.value
        db.add(user)
        db.commit()

        self._record_login_event(
            db, user.id, user.username, LoginEventType.account_disabled,
            None, None, f"Disabled by admin {operator_id}"
        )
        return True

    def force_logout(self, db: Session, user_id: int, operator_id: int) -> bool:
        """强制用户退出（需要 Token 黑名单支持，这里仅记录）"""
        self._record_login_event(
            db, user_id, None, LoginEventType.force_logout,
            None, None, f"Force logout by admin {operator_id}"
        )
        return True


enterprise_auth_service = EnterpriseAuthService()