"""用户认证服务：邮箱验证、密码重置、微信登录"""
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
import secrets
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from sqlalchemy.orm import Session

from app.models.user import User, UserStatus, UserRole, WechatUser, EmailVerificationCode, PasswordResetToken
from app.core.auth import hash_password, create_access_token
from app.core.config import get_settings
from app.core.time import utc_now

settings = get_settings()


class UserAuthService:
    """用户认证服务"""

    def send_email(self, to_email: str, subject: str, body_html: str) -> bool:
        """发送邮件（SMTP）"""
        if not settings.SMTP_HOST or not settings.SMTP_USERNAME:
            # 开发模式：打印到控制台
            print(f"[EMAIL] To: {to_email}, Subject: {subject}")
            print(f"[EMAIL] Body: {body_html}")
            return True

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
            msg["To"] = to_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body_html, "html", "utf-8"))

            if settings.SMTP_USE_SSL:
                server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT)
            else:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
                server.starttls()

            server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM_EMAIL, to_email, msg.as_string())
            server.quit()
            return True
        except Exception as e:
            print(f"[EMAIL ERROR] {e}")
            return False

    def generate_verification_code(self) -> str:
        """生成6位数字验证码"""
        return f"{random.randint(100000, 999999)}"

    def send_verification_code(self, db: Session, email: str, purpose: str = "register") -> EmailVerificationCode:
        """发送邮箱验证码"""
        code = self.generate_verification_code()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.EMAIL_VERIFY_CODE_EXPIRE_MINUTES)

        # 作废旧验证码
        db.query(EmailVerificationCode).filter(
            EmailVerificationCode.email == email,
            EmailVerificationCode.purpose == purpose,
            EmailVerificationCode.used == False,
        ).update({"used": True})

        # 创建新验证码
        verify_code = EmailVerificationCode(
            email=email,
            code=code,
            purpose=purpose,
            expires_at=expires_at,
        )
        db.add(verify_code)
        db.commit()
        db.refresh(verify_code)

        # 发送邮件
        subject = "律智检 - 邮箱验证码"
        body = f"""
        <html>
        <body>
        <p>您好，</p>
        <p>您的验证码是：<strong style="font-size: 20px; color: #1890ff;">{code}</strong></p>
        <p>验证码有效期为 {settings.EMAIL_VERIFY_CODE_EXPIRE_MINUTES} 分钟，请尽快完成验证。</p>
        <p>如非本人操作，请忽略此邮件。</p>
        </body>
        </html>
        """
        self.send_email(email, subject, body)
        return verify_code

    @staticmethod
    def _is_expired(expires_at: datetime) -> bool:
        """兼容 SQLite naive datetime 的过期判断"""
        now = utc_now()
        exp = expires_at.replace(tzinfo=None) if expires_at.tzinfo else expires_at
        return now > exp

    def verify_email_code(self, db: Session, email: str, code: str, purpose: str = "register") -> bool:
        """验证邮箱验证码"""
        verify_code = db.query(EmailVerificationCode).filter(
            EmailVerificationCode.email == email,
            EmailVerificationCode.code == code,
            EmailVerificationCode.purpose == purpose,
            EmailVerificationCode.used == False,
        ).first()

        if not verify_code:
            return False

        if self._is_expired(verify_code.expires_at):
            return False

        # 标记为已使用
        verify_code.used = True
        db.add(verify_code)
        db.commit()
        return True

    def request_password_reset(self, db: Session, email: str) -> Optional[PasswordResetToken]:
        """请求密码重置，发送重置链接到邮箱"""
        user = db.query(User).filter(User.email == email).first()
        if not user:
            return None

        # 生成重置token
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)

        # 作废旧token
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used == False,
        ).update({"used": True})

        # 创建新token
        reset_token = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=expires_at,
        )
        db.add(reset_token)
        db.commit()
        db.refresh(reset_token)

        # 发送邮件
        reset_link = f"{settings.WECHAT_REDIRECT_URI.rsplit('/', 1)[0]}/reset-password?token={token}"
        subject = "律智检 - 密码重置"
        body = f"""
        <html>
        <body>
        <p>您好 {user.full_name or user.username}，</p>
        <p>您请求重置密码。请点击下面的链接进行密码重置：</p>
        <p><a href="{reset_link}" style="color: #1890ff; font-weight: bold;">{reset_link}</a></p>
        <p>此链接有效期为 {settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} 分钟。</p>
        <p>如非本人操作，请忽略此邮件并确保账号安全。</p>
        </body>
        </html>
        """
        self.send_email(email, subject, body)
        return reset_token

    def confirm_password_reset(self, db: Session, token: str, new_password: str) -> Optional[User]:
        """凭token重置密码"""
        reset_token = db.query(PasswordResetToken).filter(
            PasswordResetToken.token == token,
            PasswordResetToken.used == False,
        ).first()

        if not reset_token:
            return None

        if self._is_expired(reset_token.expires_at):
            return None

        # 更新用户密码
        user = db.query(User).filter(User.id == reset_token.user_id).first()
        if not user:
            return None

        user.hashed_password = hash_password(new_password)
        user.password_changed_at = datetime.now(timezone.utc)
        reset_token.used = True

        db.add(user)
        db.add(reset_token)
        db.commit()
        db.refresh(user)

        # 密码重置后使该用户所有旧 token 失效。
        from app.services.auth_token_service import auth_token_service
        auth_token_service.increment_token_version(db, user)
        return user

    def get_wechat_login_url(self, state: str) -> str:
        """获取微信扫码登录URL"""
        if not settings.WECHAT_APP_ID:
            return ""

        # 微信公众平台网页授权
        redirect_uri = settings.WECHAT_REDIRECT_URI
        return (
            f"https://open.weixin.qq.com/connect/qrconnect"
            f"?appid={settings.WECHAT_APP_ID}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope=snsapi_login"
            f"&state={state}#wechat_redirect"
        )

    def wechat_callback(self, db: Session, code: str, ip_address: Optional[str] = None) -> Tuple[Optional[User], Optional[str]]:
        """处理微信回调，返回(User, JWT token)"""
        if not settings.WECHAT_APP_ID or not settings.WECHAT_APP_SECRET:
            return None, None

        # 1. 获取 access_token
        token_url = (
            f"https://api.weixin.qq.com/sns/oauth2/access_token"
            f"?appid={settings.WECHAT_APP_ID}"
            f"&secret={settings.WECHAT_APP_SECRET}"
            f"&code={code}"
            f"&grant_type=authorization_code"
        )
        try:
            token_resp = requests.get(token_url, timeout=10)
            token_data = token_resp.json()
            if "access_token" not in token_data:
                return None, None

            access_token = token_data["access_token"]
            openid = token_data["openid"]
            unionid = token_data.get("unionid")

            # 2. 获取用户信息
            userinfo_url = (
                f"https://api.weixin.qq.com/sns/userinfo"
                f"?access_token={access_token}"
                f"&openid={openid}"
            )
            userinfo_resp = requests.get(userinfo_url, timeout=10)
            userinfo = userinfo_resp.json()
            nickname = userinfo.get("nickname", "")
            avatar_url = userinfo.get("headimgurl", "")

        except Exception as e:
            print(f"[WECHAT ERROR] {e}")
            return None, None

        # 3. 查找或创建用户
        wechat_user = db.query(WechatUser).filter(WechatUser.openid == openid).first()

        if wechat_user:
            # 已绑定，直接登录
            user = db.query(User).filter(User.id == wechat_user.user_id).first()
            if not user or user.status != UserStatus.active.value:
                return None, None

            user.last_login_at = datetime.now(timezone.utc)
            user.last_login_ip = ip_address
            db.add(user)
            db.commit()
            db.refresh(user)

            token = create_access_token({"sub": user.id, "role": user.role})
            return user, token
        else:
            # 新用户，创建账号并绑定
            username = f"wx_{openid[:16]}"
            email = f"{openid}@wechat.placeholder"

            user = User(
                username=username,
                email=email,
                full_name=nickname[:128] if nickname else username,
                role=UserRole.user.value,
                status=UserStatus.active.value,
                last_login_at=datetime.now(timezone.utc),
                last_login_ip=ip_address,
            )
            db.add(user)
            db.flush()

            wechat_user = WechatUser(
                user_id=user.id,
                openid=openid,
                unionid=unionid,
                nickname=nickname[:128] if nickname else None,
                avatar_url=avatar_url[:512] if avatar_url else None,
            )
            db.add(wechat_user)
            db.commit()
            db.refresh(user)

            token = create_access_token({"sub": user.id, "role": user.role})
            return user, token


user_auth_service = UserAuthService()
