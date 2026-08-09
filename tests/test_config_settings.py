"""配置按领域拆分后的兼容性与校验测试。

覆盖：领域类可独立实例化、组合 Settings 保留原访问方式、env 覆盖、
敏感字段脱敏、生产严格校验只在生产启用、保留天数 JSON 解析与校验。
"""
import pytest

from app.core.config import (
    SENSITIVE_FIELDS,
    Settings,
    get_settings,
)
from app.core.config.database import DatabaseSettings
from app.core.config.llm import LLMSettings
from app.core.config.security import SecuritySettings


def _valid_settings_kwargs() -> dict:
    return {
        "ENVIRONMENT": "development",
        "LLM_API_KEY": "test-config-key-1234567890",
        "SECRET_KEY": "s" * 40,
    }


def test_settings_composes_all_domains():
    s = Settings(**_valid_settings_kwargs())
    # 各领域字段均可按原访问方式读取
    assert s.DATABASE_URL  # database
    assert s.LLM_MODEL == "qwen-plus"  # llm
    assert s.RAG_TOP_K == 5  # rag
    assert s.STORAGE_PROVIDER == "local"  # storage
    assert s.ALGORITHM == "HS256"  # security
    assert s.FREE_PLAN_CONSULTATION_QUOTA == 5  # payment
    assert s.SENTRY_DSN == ""  # observability
    assert s.AGENT_TOOL_TIMEOUT_SECONDS == 45  # task
    assert s.SMTP_FROM_NAME == "律智检"  # messaging
    assert s.ENVIRONMENT == "development"  # core


def test_domain_classes_standalone():
    db = DatabaseSettings()
    llm = LLMSettings()
    sec = SecuritySettings()
    assert db.DATABASE_URL
    assert llm.LLM_MODEL
    assert sec.ACCESS_TOKEN_EXPIRE_MINUTES == 60


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("RAG_TOP_K", "9")
    s = Settings(**_valid_settings_kwargs())
    assert s.RAG_TOP_K == 9


def test_composed_attribute_is_settable():
    """conftest 用 patch.object(settings, 'RAG_RERANK_ENGINE', ...) —— 必须兼容。"""
    s = Settings(**_valid_settings_kwargs())
    s.RAG_RERANK_ENGINE = "heuristic"
    assert s.RAG_RERANK_ENGINE == "heuristic"


def test_redacted_dict_masks_sensitive_fields():
    s = Settings(**_valid_settings_kwargs())
    redacted = s.redacted_dict()
    assert redacted["SECRET_KEY"].endswith("****")
    assert redacted["LLM_API_KEY"].endswith("****")
    assert "test-config-key" not in redacted["LLM_API_KEY"]
    assert "s" * 40 != redacted["SECRET_KEY"]
    assert "SECRET_KEY" in SENSITIVE_FIELDS


def test_retention_days_json_parsed():
    db = DatabaseSettings(DATABASE_ARCHIVE_RETENTION_DAYS_JSON='{"operation_logs": 180, "token_usage": 365}')
    assert db.archive_retention_days() == {"operation_logs": 180, "token_usage": 365}


def test_retention_days_json_invalid_raises():
    with pytest.raises(ValueError):
        DatabaseSettings(DATABASE_ARCHIVE_RETENTION_DAYS_JSON="not-json")
    with pytest.raises(ValueError):
        DatabaseSettings(DATABASE_ARCHIVE_RETENTION_DAYS_JSON='{"operation_logs": -5}')


def test_production_validation_raises_only_in_production():
    prod = Settings(
        ENVIRONMENT="production",
        LLM_API_KEY="test-config-key-1234567890",
        SECRET_KEY="s" * 40,
        LEGAL_DATA_ENCRYPTION_KEY="",
    )
    with pytest.raises(RuntimeError):
        prod.validate_production_or_raise()

    dev = Settings(**_valid_settings_kwargs())
    dev.validate_production_or_raise()  # 不抛


def test_required_for_production_flags_external_service_config():
    s = Settings(
        **_valid_settings_kwargs(),
        STRIPE_SECRET_KEY="sk_test_xxx",
        SMTP_HOST="smtp.example.com",
    )
    issues = s.validate_required_for_production()
    assert any("PAYMENT_CHECKOUT_BASE_URL" in i for i in issues)
    assert any("SMTP_USERNAME" in i for i in issues)


def test_get_settings_singleton():
    assert get_settings() is get_settings()


def test_engine_kwargs_pool_params_for_mysql():
    """MySQL：连接池参数全部按配置生效（pool_size/max_overflow/recycle/timeout）。"""
    from app.core.database import get_engine_kwargs
    from app.core.config import get_settings

    s = get_settings()
    kwargs = get_engine_kwargs("mysql+pymysql://u:p@localhost:3306/db")
    assert kwargs["pool_size"] == s.DATABASE_POOL_SIZE
    assert kwargs["max_overflow"] == s.DATABASE_POOL_MAX_OVERFLOW
    assert kwargs["pool_recycle"] == s.DATABASE_POOL_RECYCLE
    assert kwargs["pool_timeout"] == s.DATABASE_POOL_TIMEOUT
    assert kwargs["pool_pre_ping"] is True


def test_engine_kwargs_sqlite_skips_pool_params():
    """SQLite：不传 MySQL 专属池参数，仅配置 connect_args。"""
    from app.core.database import get_engine_kwargs

    kwargs = get_engine_kwargs("sqlite:///./data/x.db")
    assert "pool_size" not in kwargs
    assert "pool_recycle" not in kwargs
    assert "pool_timeout" not in kwargs
    assert kwargs["connect_args"]["check_same_thread"] is False


def test_idempotency_ttl_default():
    s = Settings(**_valid_settings_kwargs())
    assert s.IDEMPOTENCY_KEY_TTL_SECONDS == 86400
