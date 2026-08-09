"""Payment (Stripe) and free-tier plan quota settings."""

from pydantic import Field
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class PaymentSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    STRIPE_SECRET_KEY: str = ""
    PAYMENT_CHECKOUT_BASE_URL: str = ""
    # Stripe webhook 验签密钥（t=<ts>,v1=<hmac>）；留空则跳过验签（仅开发/测试）
    PAYMENT_WEBHOOK_SECRET: str = ""

    # M-3 免费版转化 A/B：免费档位配额参数化（B 组咨询 5→8 / 审查 2→3 / 文书 2→3）。
    # ensure_default_plans 启动时按此同步 free 计划；不改 DB schema。
    FREE_PLAN_CONSULTATION_QUOTA: int = Field(default=5, ge=0, le=100)
    FREE_PLAN_REVIEW_QUOTA: int = Field(default=2, ge=0, le=100)
    FREE_PLAN_DRAFT_QUOTA: int = Field(default=2, ge=0, le=100)
