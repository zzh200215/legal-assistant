"""P0 认证安全模型：token 撤销、刷新令牌、设备、MFA、授权快照。

安全约束：
- refresh token 明文永不入库，仅保存 SHA-256 哈希。
- TOTP secret 加密保存（AES-256-GCM），恢复码仅保存哈希。
- 不记录密码、JWT、refresh token、TOTP secret、恢复码。
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class RevokedToken(Base):
    """已撤销 access token（按 jti 精确匹配）。"""

    __tablename__ = "revoked_tokens"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    jti = Column(String(128), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_type = Column(String(16), nullable=False, default="access")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), server_default=func.now())
    revoke_reason = Column(String(64), nullable=True)


class RefreshToken(Base):
    """刷新令牌（服务端仅存哈希）。

    family_id 关联同一条轮换链；同一 refresh token 被重复使用时判定为重放，
    撤销整个 family。
    """

    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    family_id = Column(String(64), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String(64), nullable=True, index=True)
    ip_hash = Column(String(64), nullable=True)
    user_agent_hash = Column(String(64), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    used_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(String(64), nullable=True)


class AuthDevice(Base):
    """用户可信/风险设备记录（确定性特征，无外部地理位置）。"""

    __tablename__ = "auth_devices"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String(64), nullable=False, index=True)
    device_name = Column(String(128), nullable=True)
    ip_hash = Column(String(64), nullable=True)
    user_agent_hash = Column(String(64), nullable=True)
    risk_level = Column(String(16), nullable=False, default="low", index=True)
    risk_reason = Column(String(128), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MFACredential(Base):
    """TOTP MFA 凭据。secret 加密保存。"""

    __tablename__ = "mfa_credentials"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)
    secret_encrypted = Column(Text, nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_verified_at = Column(DateTime(timezone=True), nullable=True)


class MFAChallenge(Base):
    """MFA 登录挑战令牌：只能用于 MFA 验证，不能访问业务接口。"""

    __tablename__ = "mfa_challenges"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    challenge_jti = Column(String(64), unique=True, nullable=False, index=True)
    purpose = Column(String(16), nullable=False, default="mfa_login")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MFARecoveryCode(Base):
    """MFA 恢复码（仅存哈希，单次使用）。"""

    __tablename__ = "mfa_recovery_codes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    code_hash = Column(String(64), unique=True, nullable=False, index=True)
    used = Column(Boolean, nullable=False, default=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuthorizationSnapshot(Base):
    """长流程权限快照：服务端创建，客户端只能提交 snapshot_id。"""

    __tablename__ = "authorization_snapshots"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    snapshot_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    legal_role = Column(String(32), nullable=True)
    token_version = Column(Integer, nullable=False, default=0)
    jti = Column(String(128), nullable=True)
    resource_scope_json = Column(Text, nullable=True)
    explicit_shares_json = Column(Text, nullable=True)
    policy_version = Column(Integer, nullable=False, default=1)
    snapshot_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(String(64), nullable=True)
