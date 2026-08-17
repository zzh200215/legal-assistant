"""TOTP MFA 服务：secret 加密保存、恢复码单次哈希、challenge 校验。"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.encryption import decrypt_text, encrypt_text
from app.models.security_auth import MFAChallenge, MFACredential, MFARecoveryCode

settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class MFAService:
    CHALLENGE_TTL_MINUTES = 10
    RECOVERY_CODE_COUNT = 10

    # ── TOTP ─────────────────────────────────────────────────────────────────────

    def generate_secret(self) -> str:
        """生成 32 字节 Base32 TOTP secret。"""
        raw = secrets.token_bytes(20)
        return base64.b32encode(raw).decode("ascii").rstrip("=")

    def totp_code(self, secret: str, at: Optional[datetime] = None) -> str:
        """按给定时间生成 TOTP 验证码（测试用，与 _totp_verify_stdlib 同算法）。"""
        import hmac
        import struct

        ts = int((at or utc_now()).timestamp())
        counter = ts // 30
        try:
            key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
        except Exception:
            return ""
        msg = struct.pack(">Q", counter)
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
        return f"{binary % 10**6:06d}"

    def _totp_verify(self, secret: str, code: str) -> bool:
        """RFC 6238 TOTP 校验（纯标准库实现，确定性、可测试）。"""
        try:
            import pyotp  # noqa: F401

            totp = pyotp.TOTP(secret)
            return totp.verify(code.strip(), valid_window=1)
        except ImportError:
            return self._totp_verify_stdlib(secret, code)

    @staticmethod
    def _totp_verify_stdlib(secret: str, code: str) -> bool:
        import hmac
        import struct

        code = code.strip()
        if not code.isdigit() or len(code) != 6:
            return False
        try:
            key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
        except Exception:
            return False
        now = int(datetime.now().timestamp())
        for counter in (now // 30 - 1, now // 30, now // 30 + 1):
            msg = struct.pack(">Q", counter)
            digest = hmac.new(key, msg, hashlib.sha1).digest()
            offset = digest[-1] & 0x0F
            binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
            if f"{binary % 10**6:06d}" == code:
                return True
        return False

    def get_credential(self, db: Session, user_id: int) -> Optional[MFACredential]:
        return (
            db.query(MFACredential).filter(MFACredential.user_id == user_id).first()
        )

    def mfa_enabled(self, db: Session, user_id: int) -> bool:
        cred = self.get_credential(db, user_id)
        return bool(cred and cred.enabled)

    def setup(self, db: Session, user_id: int) -> tuple[MFACredential, str]:
        """开始启用 MFA：生成新 secret 并返回 otpauth URI。

        已启用状态下拒绝重置（需先 disable），避免覆盖 secret 导致锁定。
        """
        existing = self.get_credential(db, user_id)
        if existing and existing.enabled:
            raise ValueError("MFA 已启用，如需重置请先禁用")
        secret = self.generate_secret()
        cred = existing if existing is not None else MFACredential(user_id=user_id)
        if existing is None:
            db.add(cred)
        cred.secret_encrypted = encrypt_text(secret)
        cred.enabled = False  # 需 confirm 后启用
        db.add(cred)
        db.commit()
        db.refresh(cred)
        otpauth = f"otpauth://totp/LawIntelligence:{user_id}?secret={secret}&issuer=LawIntelligence"
        return cred, otpauth

    def confirm(self, db: Session, user_id: int, code: str) -> bool:
        """用一次性验证码确认启用 MFA；同时生成恢复码。"""
        cred = self.get_credential(db, user_id)
        if not cred or not cred.secret_encrypted:
            return False
        secret = decrypt_text(cred.secret_encrypted)
        if not self._totp_verify(secret, code):
            return False
        cred.enabled = True
        cred.last_verified_at = utc_now()
        db.add(cred)
        db.commit()
        return True

    def disable(self, db: Session, user_id: int) -> None:
        cred = self.get_credential(db, user_id)
        if cred:
            db.delete(cred)
        db.query(MFARecoveryCode).filter(MFARecoveryCode.user_id == user_id).delete()
        db.commit()

    def verify(self, db: Session, user_id: int, code: str) -> bool:
        """校验 TOTP 或恢复码。恢复码单次使用。"""
        if self._verify_totp(db, user_id, code):
            return True
        return self._consume_recovery_code(db, user_id, code)

    def _verify_totp(self, db: Session, user_id: int, code: str) -> bool:
        cred = self.get_credential(db, user_id)
        if not cred or not cred.enabled or not cred.secret_encrypted:
            return False
        secret = decrypt_text(cred.secret_encrypted)
        if not self._totp_verify(secret, code):
            return False
        cred.last_verified_at = utc_now()
        db.add(cred)
        db.commit()
        return True

    def _consume_recovery_code(self, db: Session, user_id: int, code: str) -> bool:
        code_hash = self._hash_code(code.strip())
        row = (
            db.query(MFARecoveryCode)
            .filter(MFARecoveryCode.user_id == user_id, MFARecoveryCode.code_hash == code_hash)
            .first()
        )
        if not row or row.used:
            return False
        row.used = True
        row.used_at = utc_now()
        db.add(row)
        db.commit()
        return True

    def generate_recovery_codes(self, db: Session, user_id: int) -> list[str]:
        """重新生成恢复码（仅存哈希）。返回明文供展示一次。"""
        db.query(MFARecoveryCode).filter(MFARecoveryCode.user_id == user_id).delete()
        plain_codes: list[str] = []
        for _ in range(self.RECOVERY_CODE_COUNT):
            code = f"{secrets.token_hex(5).upper()}"
            plain_codes.append(code)
            db.add(
                MFARecoveryCode(user_id=user_id, code_hash=self._hash_code(code))
            )
        db.commit()
        return plain_codes

    @staticmethod
    def _hash_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    # ── Challenge（登录第二步）────────────────────────────────────────────────────

    def create_challenge(self, db: Session, user_id: int) -> tuple[str, int]:
        """创建 MFA 登录 challenge token，仅可用于 MFA 验证。"""
        challenge_jti = secrets.token_urlsafe(24)
        db.add(
            MFAChallenge(
                user_id=user_id,
                challenge_jti=challenge_jti,
                purpose="mfa_login",
                expires_at=utc_now() + timedelta(minutes=self.CHALLENGE_TTL_MINUTES),
            )
        )
        db.commit()
        return challenge_jti, self.CHALLENGE_TTL_MINUTES

    def validate_challenge(self, db: Session, challenge_jti: str, user_id: int) -> bool:
        """校验并消费 challenge。只能使用一次，过期失效。"""
        row = (
            db.query(MFAChallenge)
            .filter(MFAChallenge.challenge_jti == challenge_jti)
            .first()
        )
        if not row or row.user_id != user_id or row.used:
            return False
        expires_at = _coerce_utc(row.expires_at)
        if expires_at and expires_at < utc_now():
            return False
        row.used = True
        db.add(row)
        db.commit()
        return True


mfa_service = MFAService()
