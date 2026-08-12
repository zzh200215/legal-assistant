"""LLM provider / model routing / rate-limit settings."""

import json

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG


class LLMSettings(BaseSettings):
    model_config = ENV_FILE_CONFIG

    LLM_PROVIDER: str = "openai_compatible"
    LLM_API_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_API_KEY: str = ""
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "qwen-plus"
    LLM_VISION_MODEL: str = ""
    EMBEDDING_MODEL: str = "text-embedding-v3"
    # 简单请求优先走小模型；主模型服务不可用时可降级到小模型。
    # 小模型地址和密钥留空时复用 LLM_API_BASE_URL / LLM_API_KEY。
    LLM_MODEL_ROUTING_ENABLED: bool = True
    LLM_SMALL_MODEL: str = ""
    LLM_SMALL_MODEL_PROVIDER: str = "openai_compatible"
    LLM_SMALL_MODEL_API_BASE_URL: str = ""
    LLM_SMALL_MODEL_API_KEY: str = ""
    LLM_SIMPLE_REQUEST_MAX_CHARS: int = Field(default=600, ge=64, le=4000)
    LLM_PRIMARY_REQUEST_RETRIES: int = Field(default=2, ge=1, le=5)
    LLM_FALLBACK_REQUEST_RETRIES: int = Field(default=1, ge=1, le=3)
    LLM_REQUEST_TIMEOUT_SECONDS: int = Field(default=60, ge=5, le=180)
    LLM_MODEL_FALLBACK_ENABLED: bool = True
    LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY: bool = True
    LLM_ROUTING_ALERT_MIN_REQUESTS: int = Field(default=10, ge=1, le=10000)
    LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE: float = Field(default=0.2, ge=0.0, le=1.0)
    LLM_ROUTING_ALERT_FALLBACK_FAILURE_RATE: float = Field(default=0.3, ge=0.0, le=1.0)
    LLM_PRICE_CURRENCY: str = "CNY"
    LLM_MODEL_PRICING: str = (
        '{"qwen-plus":{"input_per_1k":0.004,"output_per_1k":0.012},'
        '"text-embedding-v3":{"input_per_1k":0.0005,"output_per_1k":0.0}}'
    )
    LLM_RATE_LIMIT_WINDOW_SECONDS: int = 60
    LLM_RATE_LIMIT_MAX_REQUESTS: int = 20
    LLM_DAILY_REQUEST_LIMIT: int = 200
    LLM_DAILY_TOKEN_LIMIT: int = 300000
    LLM_ESTIMATED_CHARS_PER_TOKEN: int = 4
    LLM_ESTIMATED_COMPLETION_TOKENS: int = 1200
    LLM_LIMIT_REDIS_PREFIX: str = "aibg:llm-governance"

    # 供应商熔断：仅超时/传输/5xx 计入；参数/鉴权/权限/内容拦截不计入。
    CIRCUIT_BREAKER_ENABLED: bool = True
    CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = Field(default=5, ge=1, le=100)
    CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = Field(default=30.0, ge=0.0, le=3600.0)
    CIRCUIT_BREAKER_HALF_OPEN_MAX_CONCURRENCY: int = Field(default=1, ge=1, le=10)
    # 可选 Redis 后端（默认关闭，进程内即可运行）；跨实例不承诺强一致。
    CIRCUIT_BREAKER_REDIS_ENABLED: bool = False
    CIRCUIT_BREAKER_REDIS_PREFIX: str = "aibg:circuit-breaker"

    # 对话记忆调优参数
    CONVERSATION_MEMORY_RECENT_MESSAGES: int = Field(default=12, ge=4, le=40)
    CONVERSATION_MEMORY_SUMMARY_TRIGGER: int = Field(default=18, ge=8, le=100)
    CONVERSATION_MEMORY_SUMMARY_MAX_CHARS: int = Field(default=2400, ge=400, le=8000)
    CONVERSATION_MEMORY_MAX_PREFERENCES: int = Field(default=12, ge=1, le=50)
    CONVERSATION_MEMORY_AUTO_PREFERENCE_ENABLED: bool = True
    CONVERSATION_MEMORY_AUTO_PREFERENCE_MAX_ITEMS: int = Field(default=3, ge=1, le=5)

    @field_validator("LLM_API_KEY")
    @classmethod
    def validate_llm_api_key(cls, v: str) -> str:
        if not v or v in {"your-api-key", "your-dashscope-api-key", "sk-xxxxx"}:
            raise ValueError(
                "LLM_API_KEY必须配置有效的API密钥。请在.env文件中设置正确的值。"
            )
        if len(v) < 16:
            raise ValueError("LLM_API_KEY长度不足，请检查是否正确配置")
        return v

    @field_validator("LLM_MODEL_PRICING")
    @classmethod
    def validate_pricing_json(cls, v: str) -> str:
        try:
            pricing = json.loads(v)
            if not isinstance(pricing, dict):
                raise ValueError("LLM_MODEL_PRICING必须是有效的JSON对象")
            for model, prices in pricing.items():
                if not isinstance(prices, dict):
                    raise ValueError(f"模型 {model} 的定价配置必须是对象")
                if "input_per_1k" not in prices or "output_per_1k" not in prices:
                    raise ValueError(f"模型 {model} 缺少必需的定价字段")
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM_MODEL_PRICING格式错误：{e}")
        return v
