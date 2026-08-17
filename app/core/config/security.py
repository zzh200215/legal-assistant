"""Auth / encryption / external-idp security settings."""

import json

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class SecuritySettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    # P1-D JWT issuer/audience（可选）：配置后签发强制写入、校验强制核对；
    # 未配置则不强制（兼容存量 token），生产建议配置（见 docs/security-testing-attack-surface.md）。
    JWT_ISSUER: str = ""
    JWT_AUDIENCE: str = ""
    # 逗号分隔的 IP 网段/地址列表；只有来自这些代理的请求才解析 X-Forwarded-For。
    TRUSTED_PROXIES: str = ""
    CONNECTOR_CREDENTIAL_ENCRYPTION_KEY: str = ""

    # 企业微信扫码登录
    WECOM_CORP_ID: str = ""
    WECOM_AGENT_ID: str = ""
    WECOM_SECRET: str = ""

    # 钉钉配置
    DINGTALK_APP_KEY: str = ""
    DINGTALK_APP_SECRET: str = ""

    # LDAP 配置
    LDAP_URL: str = ""
    LDAP_BASE_DN: str = ""
    LDAP_BIND_DN: str = ""
    LDAP_BIND_PASSWORD: str = ""

    # 登录安全配置
    LOGIN_MAX_FAIL_COUNT: int = 5
    LOGIN_LOCK_DURATION_MINUTES: int = 30

    # 微信公众号扫码登录
    WECHAT_APP_ID: str = ""
    WECHAT_APP_SECRET: str = ""
    WECHAT_REDIRECT_URI: str = ""

    # 电子签名回调验签：JSON 对象，键为 fadada / esigncn，值为对应 HMAC 密钥。
    SIGNING_WEBHOOK_SECRETS_JSON: str = ""
    SIGNING_FADADA_SANDBOX_URL: str = ""
    SIGNING_FADADA_API_KEY: str = ""
    # ── P1-C 统一 Webhook 验签：时间戳窗口与 nonce 去重 TTL ──────────────────
    # 入站 Webhook（签署/支付/飞书）统一经 app/core/webhook_verifier.py 验签；
    # 时间戳新鲜度窗口（秒），过期拒绝；nonce 去重 TTL（秒），DB 共享存储惰性清理。
    WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS: int = Field(default=300, ge=30, le=3600)
    WEBHOOK_REPLAY_TTL_SECONDS: int = Field(default=3600, ge=60, le=86400)
    LEGAL_DATA_ENCRYPTION_KEY: str = ""
    LEGAL_DATA_ENCRYPTION_KEYS_JSON: str = ""
    LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION: str = "v1"

    # ── P1-A 密钥管理：统一 SecretProvider 选择与 KMS 占位配置 ──────────────
    # 统一密钥提供方：env（默认，环境变量/密钥环 JSON）/ kms（云 KMS/Secret Manager 适配器骨架）。
    # 注意：kms 为可插拔骨架，未配置 SECRET_KMS_REGION/SECRET_KMS_ENDPOINT 时构造即报错，
    # 不伪造"已接入真实云 KMS"（见 docs/secret-management.md）。
    SECRET_PROVIDER: str = Field(default="env", pattern="^(env|kms)$")
    SECRET_KMS_REGION: str = ""
    SECRET_KMS_ENDPOINT: str = ""
    SECRET_KMS_PREFIX: str = "aibg"

    # 初始管理员账号
    ADMIN_USERNAME: str = ""
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if v in {"", "replace-with-a-random-secret", "replace-with-a-long-random-secret"}:
            raise ValueError(
                "SECRET_KEY必须设置为强随机值。请在.env文件中配置至少32字符的随机字符串。"
            )
        if len(v) < 32:
            raise ValueError("SECRET_KEY长度至少需要32字符以确保安全性")
        return v

    @model_validator(mode="after")
    def validate_jwt_boundaries(self) -> "SecuritySettings":
        if bool(self.JWT_ISSUER) != bool(self.JWT_AUDIENCE):
            raise ValueError("JWT_ISSUER 与 JWT_AUDIENCE 必须同时配置或同时留空")
        return self

    @field_validator("SIGNING_WEBHOOK_SECRETS_JSON")
    @classmethod
    def validate_webhook_secrets_json(cls, v: str) -> str:
        if not v:
            return v
        try:
            secrets = json.loads(v)
            if not isinstance(secrets, dict):
                raise ValueError("SIGNING_WEBHOOK_SECRETS_JSON必须是有效的JSON对象")
        except json.JSONDecodeError as e:
            raise ValueError(f"SIGNING_WEBHOOK_SECRETS_JSON格式错误：{e}")
        return v

    @model_validator(mode="after")
    def validate_encryption_keys(self) -> "SecuritySettings":
        if self.CONNECTOR_CREDENTIAL_ENCRYPTION_KEY:
            # 验证是否为有效的Fernet密钥格式
            if len(self.CONNECTOR_CREDENTIAL_ENCRYPTION_KEY) < 32:
                raise ValueError("CONNECTOR_CREDENTIAL_ENCRYPTION_KEY长度不足")

        if self.LEGAL_DATA_ENCRYPTION_KEY:
            if len(self.LEGAL_DATA_ENCRYPTION_KEY) < 32:
                raise ValueError("LEGAL_DATA_ENCRYPTION_KEY长度不足，需要32字节URL-safe Base64密钥")
            if self.SECRET_KEY and self.LEGAL_DATA_ENCRYPTION_KEY == self.SECRET_KEY:
                raise ValueError(
                    "LEGAL_DATA_ENCRYPTION_KEY不得复用SECRET_KEY：法律数据加密必须使用独立的密钥"
                )

        if self.LEGAL_DATA_ENCRYPTION_KEYS_JSON:
            try:
                keys = json.loads(self.LEGAL_DATA_ENCRYPTION_KEYS_JSON)
                if not isinstance(keys, dict) or not keys:
                    raise ValueError("必须是非空对象")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("LEGAL_DATA_ENCRYPTION_KEYS_JSON格式错误") from exc

        return self
