from functools import lru_cache
from typing import Optional
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import json
import os


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./data/app.db"
    DATABASE_POOL_PRE_PING: bool = True
    DATABASE_ECHO: bool = False
    # E-7：MySQL 连接池尺寸（pool_size + max_overflow = 上限）。
    # LLM 调用期间请求不持有连接后，pool_size 覆盖同时进行中的请求数即可；
    # 每个请求瞬时最多占用 2 个连接（主 session + LLM 用量记录 session）。
    DATABASE_POOL_SIZE: int = Field(default=20, ge=1, le=200)
    DATABASE_POOL_MAX_OVERFLOW: int = Field(default=40, ge=0, le=400)
    REDIS_URL: str = "redis://localhost:6379/0"
    SECRET_KEY: str = ""
    CONNECTOR_CREDENTIAL_ENCRYPTION_KEY: str = ""
    MAILBOX_RETENTION_DAYS: int = Field(default=90, ge=7, le=3650)
    ALERT_WEBHOOK_URL: str = ""
    ALERT_WEBHOOK_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=30)
    ALERT_WEBHOOK_MIN_SEVERITY: str = "high"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
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
    AGENT_TOOL_TIMEOUT_SECONDS: int = Field(default=45, ge=5, le=300)
    AGENT_PARALLEL_MAX_WORKERS: int = Field(default=2, ge=1, le=4)
    CONVERSATION_MEMORY_RECENT_MESSAGES: int = Field(default=12, ge=4, le=40)
    CONVERSATION_MEMORY_SUMMARY_TRIGGER: int = Field(default=18, ge=8, le=100)
    CONVERSATION_MEMORY_SUMMARY_MAX_CHARS: int = Field(default=2400, ge=400, le=8000)
    CONVERSATION_MEMORY_MAX_PREFERENCES: int = Field(default=12, ge=1, le=50)
    CONVERSATION_MEMORY_AUTO_PREFERENCE_ENABLED: bool = True
    CONVERSATION_MEMORY_AUTO_PREFERENCE_MAX_ITEMS: int = Field(default=3, ge=1, le=5)
    CONVERSATION_MEMORY_AUTO_PREFERENCE_ENABLED: bool = True
    CONVERSATION_MEMORY_AUTO_PREFERENCE_MAX_ITEMS: int = Field(default=3, ge=1, le=5)
    VECTOR_STORE_PROVIDER: str = "chroma"
    VECTOR_STORE_COLLECTION_NAME: str = "documents"
    # Keep legal articles in an independent collection so document RAG and
    # legal retrieval never share tenant metadata or lifecycle operations.
    LEGAL_VECTOR_STORE_COLLECTION_NAME: str = "legal_articles"
    CHROMA_PERSIST_DIR: str = "./data/chroma_db"
    QDRANT_PERSIST_DIR: str = "./qdrant_db"
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str = ""
    NEO4J_ENABLED: bool = False
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: str = ""
    NEO4J_DATABASE: str = "neo4j"
    NEO4J_MAX_CONNECTION_POOL_SIZE: int = Field(default=20, ge=1, le=100)
    LEGAL_GRAPH_EVIDENCE_BOOST: float = Field(default=0.001, ge=0.0, le=0.01)
    LEGAL_GRAPH_EVIDENCE_MAX_SUPPORT_COUNT: int = Field(default=3, ge=1, le=10)
    OCR_ENABLE_IMAGE_PREPROCESS: bool = True
    OCR_PDF_RENDER_DPI: int = 200
    OCR_MIN_TEXT_LENGTH: int = 24
    OCR_MIN_READABLE_RATIO: float = 0.45
    MEETING_ASR_ENABLED: bool = True
    MEETING_ASR_MODEL: str = "small"
    MEETING_ASR_DEVICE: str = "cpu"
    MEETING_ASR_COMPUTE_TYPE: str = "int8"
    MEETING_ASR_DOWNLOAD_ROOT: str = ""
    RAG_TOP_K: int = 5
    RAG_CONFIDENCE_THRESHOLD: float = 0.35
    RAG_MIN_RECALL_CANDIDATES: int = 8
    RAG_RECALL_MULTIPLIER: int = 3
    RAG_QUERY_VARIANT_LIMIT: int = 4
    RAG_CONTEXT_NEIGHBOR_WINDOW: int = 1
    RAG_CONTEXT_MAX_CHUNKS: int = 8
    AGENTIC_RAG_ENABLED: bool = True
    AGENTIC_RAG_PLANNER_ENABLED: bool = True
    AGENTIC_RAG_MAX_RETRIEVAL_ROUNDS: int = Field(default=2, ge=1, le=3)
    ADMIN_USERNAME: str = ""
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""

    # 企业微信配置
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

    # 微信公众号扫码登录
    WECHAT_APP_ID: str = ""
    WECHAT_APP_SECRET: str = ""
    WECHAT_REDIRECT_URI: str = ""

    STORAGE_PROVIDER: str = "local"
    STORAGE_LOCAL_DIR: str = "./data/uploads"
    VITE_WS_HOST: str = "localhost:8001"

    # 支付配置
    STRIPE_SECRET_KEY: str = ""
    PAYMENT_CHECKOUT_BASE_URL: str = ""
    # Stripe webhook 验签密钥（t=<ts>,v1=<hmac>）；留空则跳过验签（仅开发/测试）
    PAYMENT_WEBHOOK_SECRET: str = ""

    # 电子签名回调验签：JSON 对象，键为 fadada / esigncn，值为对应 HMAC 密钥。
    SIGNING_WEBHOOK_SECRETS_JSON: str = ""
    SIGNING_FADADA_SANDBOX_URL: str = ""
    SIGNING_FADADA_API_KEY: str = ""
    LEGAL_DATA_ENCRYPTION_KEY: str = ""
    LEGAL_DATA_ENCRYPTION_KEYS_JSON: str = ""
    LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION: str = "v1"
    # 开放平台异步任务（P0-05）：任务消费者尚未上线，保持关闭，拒绝 /v1/contract-reviews 提交。
    OPEN_API_ENABLED: bool = False
    ENVIRONMENT: str = "development"

    # E-5 可观测性：Sentry 错误上报 + OpenTelemetry 链路追踪（留空/关闭时跳过初始化）
    SENTRY_DSN: str = ""
    OTEL_ENABLED: bool = False
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

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
    def validate_database_config(self) -> "Settings":
        if not self.DATABASE_URL or self.DATABASE_URL == "sqlite:///./data/app.db":
            import warnings
            warnings.warn(
                "使用默认SQLite数据库，生产环境请配置MySQL/PostgreSQL",
                UserWarning
            )
        return self

    @model_validator(mode="after")
    def validate_encryption_keys(self) -> "Settings":
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

        if self.ENVIRONMENT.lower() in {"pilot", "production", "prod"} and not (
            self.LEGAL_DATA_ENCRYPTION_KEY or self.LEGAL_DATA_ENCRYPTION_KEYS_JSON
        ):
            raise ValueError("试点/生产环境必须配置独立的LEGAL_DATA_ENCRYPTION_KEY或版本化密钥环")

        return self

    def get_env_file_path(self) -> Optional[str]:
        """返回实际使用的.env文件路径"""
        env_path = ".env"
        if os.path.exists(env_path):
            return os.path.abspath(env_path)
        return None

    def validate_required_for_production(self) -> list[str]:
        """检查生产环境必需配置，返回缺失项列表"""
        issues = []

        if not self.LLM_API_KEY:
            issues.append("LLM_API_KEY未配置")

        if self.DATABASE_URL == "sqlite:///./data/app.db":
            issues.append("生产环境应使用MySQL/PostgreSQL而非SQLite")

        if not self.REDIS_URL:
            issues.append("REDIS_URL未配置")

        if not self.ADMIN_USERNAME or not self.ADMIN_PASSWORD:
            issues.append("管理员账号未配置（ADMIN_USERNAME/ADMIN_PASSWORD）")
        if self.ENVIRONMENT.lower() in {"production", "prod", "pilot"} and not (
            self.LEGAL_DATA_ENCRYPTION_KEY or self.LEGAL_DATA_ENCRYPTION_KEYS_JSON
        ):
            issues.append("试点/生产环境必须配置独立的LEGAL_DATA_ENCRYPTION_KEY或版本化密钥环")

        return issues


@lru_cache()
def get_settings() -> Settings:
    """获取应用配置单例"""
    try:
        s = Settings()
        return s
    except ValueError as e:
        # 配置验证失败，提供清晰的错误信息
        raise RuntimeError(
            f"配置加载失败：{e}\n"
            f"请检查.env文件并确保所有必需配置正确设置。"
        ) from e
    except Exception as e:
        raise RuntimeError(
            f"配置初始化错误：{e}\n"
            f"请确保.env文件存在且格式正确。"
        ) from e


def check_config_health() -> dict:
    """检查配置健康状态，返回诊断信息"""
    result = {
        "status": "healthy",
        "issues": [],
        "warnings": [],
        "env_file": None,
    }

    try:
        settings = get_settings()
        result["env_file"] = settings.get_env_file_path()

        # 检查生产环境必需配置
        prod_issues = settings.validate_required_for_production()
        if prod_issues:
            result["warnings"].extend(prod_issues)

        # 检查API连通性相关配置
        if not settings.LLM_API_BASE_URL:
            result["issues"].append("LLM_API_BASE_URL未配置")

        if settings.VECTOR_STORE_PROVIDER == "qdrant" and not settings.QDRANT_URL:
            result["issues"].append("使用Qdrant但未配置QDRANT_URL")

        # 更新总体状态
        if result["issues"]:
            result["status"] = "unhealthy"
        elif result["warnings"]:
            result["status"] = "warning"

    except Exception as e:
        result["status"] = "error"
        result["issues"].append(f"配置加载失败: {str(e)}")

    return result
