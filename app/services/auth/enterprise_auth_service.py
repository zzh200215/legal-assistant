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
from app.core.auth import hash_password
from app.core.config import get_settings


settings = get_settings()


def _escape_ldap_filter_value(value: str) -> str:
    """按 RFC 4515 转义 LDAP 过滤器中的用户输入，防止 LDAP 注入。"""
    return (
        value.replace("\\", "\\5c")
        .replace("*", "\\2a")
        .replace("(", "\\28")
        .replace(")", "\\29")
        .replace("\x00", "\\00")
    )


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
        """同步组织架构（演示环境无外部组织，返回空；接入真实目录后按需实现）"""
        return []

    def sync_department(self, db: Session, org_code: str) -> list[Department]:
        """同步部门（同上，安全默认）"""
        return []

    def _simulate_user(self, seed: str, username: str | None = None) -> dict:
        """演示模式：无外部凭据时按 seed 确定性生成一个真实感企业用户。

        同一 seed 恒定得到同一 external_user_id，重复登录复用既有绑定用户。
        """
        digest = hashlib.sha256(f"{self.provider_name}:{seed}".encode()).hexdigest()
        uid = digest[:12]
        return {
            "external_user_id": f"{self.provider_name}_{uid}",
            "username": username or f"{self.provider_name}_{uid[:8]}",
            "email": f"{self.provider_name}_{uid[:8]}@enterprise.example.com",
            "full_name": "演示企业用户",
            "employee_id": f"EMP-{uid[:8].upper()}",
            "department_code": "LEGAL",
            "organization_code": "PILOT-01",
        }


class WeComAuthProvider(EnterpriseAuthProvider):
    """企业微信认证"""
    provider_name = "wecom"

    def __init__(self):
        self.corp_id = settings.WECOM_CORP_ID or ""
        self.agent_id = settings.WECOM_AGENT_ID or ""
        self.secret = settings.WECOM_SECRET or ""
        self._access_token_cache: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    @property
    def _configured(self) -> bool:
        return bool(self.corp_id and self.secret)

    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        # 企业微信 OAuth2 授权 URL
        return (
            f"https://open.weixin.qq.com/connect/oauth2/authorize"
            f"?appid={self.corp_id}&redirect_uri={redirect_uri}"
            f"&response_type=code&scope=snsapi_base&state={state}#wechat_redirect"
        )

    def _get_access_token(self) -> str:
        """获取企业微信 access_token（配置凭据时走真实 API，否则演示用固定值）"""
        if self._access_token_cache and self._token_expires_at:
            if datetime.now(timezone.utc) < self._token_expires_at:
                return self._access_token_cache
        if not self._configured:
            return "demo_wecom_access_token"
        import requests
        resp = requests.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": self.corp_id, "corpsecret": self.secret}, timeout=8,
        )
        data = resp.json()
        token = data.get("access_token", "")
        if token:
            expires_in = int(data.get("expires_in", 7200))
            self._access_token_cache = token
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 300))
        return token

    def get_user_info(self, code: str) -> dict:
        """通过 code 获取用户信息。无凭据时走演示模式（确定性模拟用户）。"""
        if not self._configured:
            return self._simulate_user(code) if code else {}
        import requests
        token = self._get_access_token()
        resp = requests.get(
            "https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo",
            params={"access_token": token, "code": code}, timeout=8,
        )
        data = resp.json()
        userid = data.get("UserId") or data.get("userid")
        if not userid:
            return {}
        detail = requests.get(
            "https://qyapi.weixin.qq.com/cgi-bin/user/get",
            params={"access_token": token, "userid": userid}, timeout=8,
        ).json()
        departments = detail.get("department") or []
        return {
            "external_user_id": userid,
            "username": userid,
            "email": detail.get("email") or f"{userid}@wecom.example.com",
            "full_name": detail.get("name"),
            "employee_id": detail.get("employee_no"),
            "department_code": str(departments[0]) if departments else "",
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

    @property
    def _configured(self) -> bool:
        return bool(self.app_key and self.app_secret)

    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        return (
            f"https://login.dingtalk.com/oauth2/auth"
            f"?redirect_uri={redirect_uri}&client_id={self.app_key}"
            f"&response_type=code&scope=openid&state={state}&prompt=consent"
        )

    def _get_access_token(self) -> str:
        """获取钉钉 access_token（配置凭据时走真实 API，否则演示用固定值）"""
        if self._access_token_cache and self._token_expires_at:
            if datetime.now(timezone.utc) < self._token_expires_at:
                return self._access_token_cache
        if not self._configured:
            return "demo_dingtalk_access_token"
        import requests
        resp = requests.post(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={"appKey": self.app_key, "appSecret": self.app_secret}, timeout=8,
        )
        data = resp.json()
        token = data.get("accessToken", "")
        if token:
            self._access_token_cache = token
            self._token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=7100)
        return token

    def get_user_info(self, code: str) -> dict:
        """通过 code 获取用户信息。无凭据时走演示模式。"""
        if not self._configured:
            return self._simulate_user(code) if code else {}
        import requests
        # code 换 user access token
        token_resp = requests.post(
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
            json={"clientId": self.app_key, "clientSecret": self.app_secret, "code": code}, timeout=8,
        ).json()
        union_id = token_resp.get("unionId")
        if not union_id:
            return {}
        # unionId 拉用户详情
        detail = requests.get(
            f"https://api.dingtalk.com/v1.0/contact/users/{union_id}",
            headers={"x-acs-dingtalk-access-token": self._get_access_token()}, timeout=8,
        ).json()
        return {
            "external_user_id": union_id,
            "username": detail.get("nick") or union_id,
            "email": detail.get("email") or "",
            "full_name": detail.get("name") or detail.get("nick"),
            "employee_id": detail.get("employeeId"),
            "department_code": str(detail["deptIdList"][0]) if detail.get("deptIdList") else "",
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

    @property
    def _configured(self) -> bool:
        return bool(self.ldap_url)

    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        # LDAP 不需要跳转，直接表单登录
        return ""

    def verify_credentials(self, username: str, password: str) -> Optional[dict]:
        """验证 LDAP 用户凭据。

        配置了 LDAP_URL 时走 ldap3 bind + search（需安装 ldap3）；
        未配置时走演示模式：接受非空凭据，返回按用户名确定性生成的用户。
        """
        if not self._configured:
            if not username or not password:
                return None
            return self._simulate_user(username, username=username)
        try:
            from ldap3 import SUBTREE, Server, Connection
        except ImportError as exc:  # pragma: no cover - 演示环境不装 ldap3
            raise RuntimeError("LDAP 登录需要安装 ldap3 库") from exc
        server = Server(self.ldap_url)
        with Connection(server, user=self.ldap_bind_dn, password=self.ldap_bind_password, auto_bind=True) as conn:
            user_filter = (
                "(&(objectClass=person)(|"
                f"(uid={_escape_ldap_filter_value(username)})"
                f"(sAMAccountName={_escape_ldap_filter_value(username)})))"
            )
            conn.search(self.ldap_base_dn, user_filter, SUBTREE, attributes=["uid", "mail", "displayName", "employeeNumber", "departmentNumber"])
            if not conn.entries:
                return None
            entry = conn.entries[0]
            # 复用已绑定连接验证用户密码；auto_bind=True 绑定失败会抛 LDAPBindError，
            # 捕获后按认证失败返回 None（原 `if not Connection(...)` 分支是死代码）。
            try:
                with Connection(server, user=entry.entry_dn, password=password, auto_bind=True):
                    pass
            except Exception:  # noqa: BLE001 - ldap3 绑定失败统一按凭据无效处理
                return None
            attrs = {k: str(v) for k, v in entry.entry_attributes_as_dict.items()}
        return {
            "external_user_id": attrs.get("uid") or username,
            "username": username,
            "email": attrs.get("mail") or f"{username}@ldap.example.com",
            "full_name": attrs.get("displayName"),
            "employee_id": attrs.get("employeeNumber"),
            "department_code": attrs.get("departmentNumber"),
            "organization_code": "",
        }

    def get_user_info(self, code: str) -> dict:
        # LDAP 不使用 code 模式
        return {}


class EnterpriseAuthService:
    """企业统一登录服务"""

    MAX_LOGIN_FAIL_COUNT = 5
    LOCK_DURATION_MINUTES = 30

    def __init__(self):
        # 三个 Provider 全量注册：配置了凭据走真实 API，未配置走演示模拟模式
        self.providers: dict[str, EnterpriseAuthProvider] = {
            "wecom": WeComAuthProvider(),
            "dingtalk": DingTalkAuthProvider(),
            "ldap": LDAPAuthProvider(),
        }

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

        # 本方法只负责凭据验证；真实会话（access+refresh+设备记录）由 API 层
        # _issue_login_response → auth_token_service.issue_session 统一签发，
        # 此处不再铸造无法撤销的孤儿 access token。
        return user, "ok"

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

        # 本方法只负责凭据验证；真实会话（access+refresh+设备记录）由 API 层
        # _issue_login_response → auth_token_service.issue_session 统一签发，
        # 此处不再铸造无法撤销的孤儿 access token。
        return user, "ok"

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

        # 本方法只负责凭据验证；真实会话（access+refresh+设备记录）由 API 层
        # _issue_login_response → auth_token_service.issue_session 统一签发，
        # 此处不再铸造无法撤销的孤儿 access token。
        return user, "ok"

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
            # OAuth/LDAP 用户无本地可登录密码：随机占位满足 MySQL NOT NULL，本地密码登录必然失败
            hashed_password=hash_password(secrets.token_urlsafe(32)),
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

        # 禁用后立即递增 token 版本，使该用户已有 access token 全部失效。
        from app.services.auth.auth_token_service import auth_token_service
        auth_token_service.increment_token_version(db, user)

        self._record_login_event(
            db, user.id, user.username, LoginEventType.account_disabled,
            None, None, f"Disabled by admin {operator_id}"
        )
        return True

    def force_logout(self, db: Session, user_id: int, operator_id: int) -> bool:
        """强制用户退出：递增 token 版本使旧 token 全部失效，并撤销 refresh token。"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return False

        from app.services.auth.auth_token_service import auth_token_service
        auth_token_service.increment_token_version(db, user)

        self._record_login_event(
            db, user_id, None, LoginEventType.force_logout,
            None, None, f"Force logout by admin {operator_id}"
        )
        return True


enterprise_auth_service = EnterpriseAuthService()