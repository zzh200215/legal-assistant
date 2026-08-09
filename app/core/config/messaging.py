"""Email / Feishu messaging settings."""

from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class MessagingSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    # SMTP 邮件发送配置
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "律智检"
    SMTP_USE_SSL: bool = True
    EMAIL_VERIFY_CODE_EXPIRE_MINUTES: int = 15
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # 飞书事件回调 encrypt_key（#87/M1 前置）
    FEISHU_EVENT_ENCRYPT_KEY: str = ""
    # 飞书回调验签模式（指南 §6）：auto=按 V2→V1→hex 顺序任一通过即有效；v2/v1=仅对应算法；off=跳过验签（临时排查）。
    # 注：V2 签名串拼接需以接入时飞书官方文档复核。
    FEISHU_CALLBACK_VERIFY: str = "auto"
    # 飞书企业自建应用凭据（M1 出站消息；留空则出站禁用、回调仍可解密验签）
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
