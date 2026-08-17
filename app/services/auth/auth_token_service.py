"""认证 token 生命周期服务：签发（jti/token_version）、撤销、刷新轮换、风险识别。

安全约束：
- access JWT 至少包含 sub/jti/token_version/typ=access/iat/exp。
- refresh token 为高熵随机不透明字符串，数据库只保存 SHA-256 哈希。
- 同一 refresh token 重复使用判定为重放，撤销整个 family。
- 可信代理才解析 X-Forwarded-For，否则一律使用直连 IP。
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.user import User, UserStatus
from app.models.security_auth import AuthDevice, RefreshToken, RevokedToken

settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def hash_opaque(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_jti() -> str:
    return secrets.token_urlsafe(16)


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def new_family_id() -> str:
    return secrets.token_urlsafe(16)


def new_device_id() -> str:
    return secrets.token_urlsafe(16)


def is_trusted_proxy(ip: str) -> bool:
    """只有来自配置的可信代理才解析 X-Forwarded-For。"""
    if not settings.TRUSTED_PROXIES:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for raw in settings.TRUSTED_PROXIES.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            if "/" in raw:
                if addr in ipaddress.ip_network(raw, strict=False):
                    return True
            elif addr == ipaddress.ip_address(raw):
                return True
        except ValueError:
            continue
    return False


def get_client_ip(request) -> Optional[str]:
    """从请求解析客户端 IP；仅信任可信代理转发头。"""
    if request is None:
        return None
    peer = request.client.host if request.client else None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded and peer and is_trusted_proxy(peer):
        return forwarded.split(",")[0].strip()
    return peer


@dataclass
class RiskAssessment:
    """确定性设备/IP 风险检测结果。"""

    risk_level: str = "low"  # low / medium / high
    reasons: list[str] = field(default_factory=list)
    requires_mfa: bool = False

    def merge(self, level: str, reason: str) -> None:
        order = {"low": 0, "medium": 1, "high": 2}
        if order[level] > order[self.risk_level]:
            self.risk_level = level
        self.reasons.append(reason)


class AuthTokenService:
    """签发 / 校验 / 撤销 access & refresh token。"""

    def create_access_token(
        self,
        user: User,
        *,
        token_version: Optional[int] = None,
        expires_minutes: Optional[int] = None,
        typ: str = "access",
        jti: Optional[str] = None,
    ) -> str:
        """签发 access JWT（含 sub/jti/token_version/typ/iat/exp）。"""
        from jose import jwt

        version = token_version if token_version is not None else (user.token_version or 0)
        now = utc_now()
        expire = now + timedelta(
            minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
        payload = {
            "sub": str(user.id),
            "jti": jti or new_jti(),
            "token_version": version,
            "typ": typ,
            "iat": int(now.timestamp()),
            "exp": int(expire.timestamp()),
        }
        # P1-D：配置了 issuer/audience 时签发强制写入（校验端同步强制核对）。
        if settings.JWT_ISSUER:
            payload["iss"] = settings.JWT_ISSUER
        if settings.JWT_AUDIENCE:
            payload["aud"] = settings.JWT_AUDIENCE
        return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    def decode_access_token(self, token: str) -> Optional[dict]:
        from jose import JWTError, jwt

        options: dict = {}
        kwargs: dict = {}
        if settings.JWT_ISSUER:
            options["verify_iss"] = True
            kwargs["issuer"] = settings.JWT_ISSUER
        if settings.JWT_AUDIENCE:
            options["verify_aud"] = True
            kwargs["audience"] = settings.JWT_AUDIENCE
        try:
            return jwt.decode(
                token, settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM], options=options, **kwargs,
            )
        except JWTError:
            return None

    def validate_access_token(self, token: str, db: Session) -> Optional[User]:
        """校验 access token 的签名、撤销、版本与用户状态。

        返回 None 表示 token 无效；由调用方按 401 处理。
        """
        payload = self.decode_access_token(token)
        if not payload:
            return None
        if payload.get("typ") not in (None, "access"):
            return None
        jti = payload.get("jti")
        sub = payload.get("sub")
        if not jti or not sub:
            return None
        try:
            user_id = int(sub)
        except (TypeError, ValueError):
            return None
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            return None
        if self.is_jti_revoked(jti, db):
            return None
        if int(payload.get("token_version", 0)) != (user.token_version or 0):
            return None
        return user

    def is_jti_revoked(self, jti: str, db: Session) -> bool:
        return db.query(RevokedToken).filter(RevokedToken.jti == jti).first() is not None

    # ── 会话签发（登录统一入口） ─────────────────────────────────────────────────

    def issue_session(
        self,
        db: Session,
        user: User,
        *,
        device_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        login_failures: int = 0,
        risk: Optional[RiskAssessment] = None,
    ) -> dict:
        """登录成功后统一签发 access + refresh token 并记录设备风险。

        返回 {"access_token", "refresh_token", "user", "risk_level", "risk_reasons"}。
        """
        if risk is None:
            risk = self.assess_risk(
                db,
                user,
                device_id=device_id,
                ip_address=ip_address,
                user_agent=user_agent,
                login_failures=login_failures,
            )
        self.record_device(
            db,
            user,
            device_id=device_id,
            ip_address=ip_address,
            user_agent=user_agent,
            risk=risk,
        )
        access_token = self.create_access_token(user, token_version=user.token_version)
        family, raw_refresh = self.issue_refresh_token(
            db,
            user,
            device_id=device_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return {
            "access_token": access_token,
            "refresh_token": raw_refresh,
            "user": user,
            "risk_level": risk.risk_level,
            "risk_reasons": list(risk.reasons),
        }

    def default_device_id(self, user_agent: Optional[str], ip_address: Optional[str]) -> str:
        """无法从请求头获取 device_id 时生成确定性设备标识。"""
        seed = f"{user_agent or ''}|{ip_address or ''}".strip("|")
        if not seed:
            return new_device_id()
        return hash_opaque(seed)[:16]

    def revoke_access_token(self, access_token: str, db: Session, *, reason: str = "logout") -> Optional[int]:
        payload = self.decode_access_token(access_token)
        if not payload or not payload.get("jti"):
            return None
        jti = payload["jti"]
        try:
            user_id = int(payload.get("sub"))
        except (TypeError, ValueError):
            user_id = None
        existing = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
        if not existing:
            db.add(
                RevokedToken(
                    jti=jti,
                    user_id=user_id or 0,
                    token_type="access",
                    expires_at=datetime.fromtimestamp(payload.get("exp", 0), tz=timezone.utc)
                    if payload.get("exp")
                    else None,
                    revoke_reason=reason,
                )
            )
            db.commit()
        return user_id

    def increment_token_version(self, db: Session, user: User) -> None:
        """使该用户当前全部 access token 立即失效。"""
        user.token_version = (user.token_version or 0) + 1
        db.add(user)
        now = utc_now()
        rows = (
            db.query(RefreshToken)
            .filter(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > now,
            )
            .all()
        )
        for row in rows:
            row.revoked_at = now
            row.revoke_reason = "token_version_bumped"
        db.commit()

    # ── refresh token ────────────────────────────────────────────────────────────

    def issue_refresh_token(
        self,
        db: Session,
        user: User,
        *,
        device_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        family_id: Optional[str] = None,
    ) -> tuple[str, str]:
        """签发 refresh token，返回 (family_id, raw_token)。"""
        raw_token = new_refresh_token()
        family = family_id or new_family_id()
        db.add(
            RefreshToken(
                family_id=family,
                token_hash=hash_opaque(raw_token),
                user_id=user.id,
                device_id=device_id,
                ip_hash=hash_opaque(ip_address) if ip_address else None,
                user_agent_hash=hash_opaque(user_agent) if user_agent else None,
                expires_at=utc_now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            )
        )
        db.commit()
        return family, raw_token

    def rotate_refresh_token(
        self,
        db: Session,
        refresh_token: str,
        *,
        device_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Optional[dict]:
        """刷新 token 单次轮换。

        成功时返回 {"user", "access_token", "refresh_token", "family_id"}。
        发现重放（token 已 used/revoked 但记录存在）时撤销整个 family 并返回 None。
        """
        token_hash = hash_opaque(refresh_token)
        row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
        if not row:
            return None
        now = utc_now()
        if row.used_at is not None or row.revoked_at is not None:
            db.query(RefreshToken).filter(RefreshToken.family_id == row.family_id).update(
                {
                    RefreshToken.revoked_at: now,
                    RefreshToken.revoke_reason: "replay_detected",
                }
            )
            db.commit()
            return None
        expires_at = _coerce_utc(row.expires_at)
        if expires_at and expires_at < now:
            row.revoked_at = now
            row.revoke_reason = "expired"
            db.commit()
            return None
        user = db.query(User).filter(User.id == row.user_id).first()
        if not user or user.status != UserStatus.active.value:
            return None

        row.used_at = now
        db.add(row)
        db.commit()

        access_token = self.create_access_token(user, token_version=user.token_version)
        family, raw_refresh = self.issue_refresh_token(
            db,
            user,
            device_id=device_id or row.device_id,
            ip_address=ip_address,
            user_agent=user_agent,
            family_id=row.family_id,
        )
        return {
            "user": user,
            "access_token": access_token,
            "refresh_token": raw_refresh,
            "family_id": family,
        }

    def revoke_refresh_token(self, db: Session, refresh_token: str, *, reason: str = "logout") -> Optional[int]:
        token_hash = hash_opaque(refresh_token)
        row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
        if not row:
            return None
        row.revoked_at = utc_now()
        row.revoke_reason = reason
        db.add(row)
        db.commit()
        return row.user_id

    def revoke_all_devices(self, db: Session, user: User) -> int:
        """撤销用户全部 refresh token，供全部设备退出。"""
        now = utc_now()
        rows = (
            db.query(RefreshToken)
            .filter(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
            .all()
        )
        for row in rows:
            row.revoked_at = now
            row.revoke_reason = "logout_all"
        db.commit()
        return len(rows)

    # ── 设备 / 风险识别 ──────────────────────────────────────────────────────────

    def assess_risk(
        self,
        db: Session,
        user: User,
        *,
        device_id: Optional[str],
        ip_address: Optional[str],
        user_agent: Optional[str],
        login_failures: int = 0,
    ) -> RiskAssessment:
        """确定性风险检测：新设备、IP 变化、连续失败、异常刷新、高风险状态。"""
        result = RiskAssessment()

        if user.status == UserStatus.disabled.value:
            result.merge("high", "account_disabled")
        if user.status == UserStatus.locked.value:
            result.merge("high", "account_locked")
        if user.status == UserStatus.deleted.value:
            result.merge("high", "account_deleted")

        if login_failures >= 3:
            result.merge("high", "repeated_failures")
        elif login_failures >= 1:
            result.merge("medium", "recent_failure")

        if not device_id:
            result.merge("medium", "missing_device_id")

        known_device = None
        if device_id:
            known_device = (
                db.query(AuthDevice)
                .filter(AuthDevice.user_id == user.id, AuthDevice.device_id == device_id)
                .first()
            )

        if known_device is None:
            result.merge("medium", "new_device")
        else:
            if known_device.risk_level == "high":
                result.merge("high", "device_high_risk")
            if ip_address:
                known_ip = hash_opaque(ip_address)
                if known_device.ip_hash and known_device.ip_hash != known_ip:
                    result.merge("medium", "ip_changed")
            if user_agent:
                ua_hash = hash_opaque(user_agent)
                if known_device.user_agent_hash and known_device.user_agent_hash != ua_hash:
                    result.merge("medium", "user_agent_changed")

        result.requires_mfa = result.risk_level in ("medium", "high")
        return result

    def record_device(
        self,
        db: Session,
        user: User,
        *,
        device_id: Optional[str],
        ip_address: Optional[str],
        user_agent: Optional[str],
        risk: RiskAssessment,
    ) -> None:
        if not device_id:
            return
        row = (
            db.query(AuthDevice)
            .filter(AuthDevice.user_id == user.id, AuthDevice.device_id == device_id)
            .first()
        )
        now = utc_now()
        if row is None:
            db.add(
                AuthDevice(
                    user_id=user.id,
                    device_id=device_id,
                    ip_hash=hash_opaque(ip_address) if ip_address else None,
                    user_agent_hash=hash_opaque(user_agent) if user_agent else None,
                    risk_level=risk.risk_level,
                    risk_reason=";".join(risk.reasons)[:128] or None,
                    last_seen_at=now,
                )
            )
        else:
            row.last_seen_at = now
            if ip_address:
                row.ip_hash = hash_opaque(ip_address)
            if user_agent:
                row.user_agent_hash = hash_opaque(user_agent)
            if risk.risk_level:
                row.risk_level = risk.risk_level
            db.add(row)
        db.commit()


auth_token_service = AuthTokenService()
