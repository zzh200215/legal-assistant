from types import SimpleNamespace

from scripts.check_pilot_readiness import evaluate_pilot_readiness


def _settings(**overrides):
    values = {
        "ENVIRONMENT": "pilot",
        "DATABASE_URL": "mysql+pymysql://user:password@mysql:3306/aibg",
        "REDIS_URL": "redis://redis:6379/0",
        "LEGAL_DATA_ENCRYPTION_KEY": "a" * 44,
        "LEGAL_DATA_ENCRYPTION_KEYS_JSON": "",
        "ADMIN_USERNAME": "pilot_admin",
        "ADMIN_PASSWORD": "strong-password",
        "LLM_API_KEY": "a-valid-provider-key",
        "NEO4J_ENABLED": False,
        "SMTP_HOST": "",
        "SMTP_USERNAME": "",
        "SMTP_PASSWORD": "",
        "PAYMENT_CHECKOUT_BASE_URL": "",
        "SIGNING_FADADA_SANDBOX_URL": "",
        "SIGNING_FADADA_API_KEY": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_pilot_readiness_accepts_minimal_safe_internal_deployment():
    report = evaluate_pilot_readiness(_settings())

    assert report["ready"] is True
    assert report["optional"]["neo4j_graph_rag"] is False
    assert report["disabled_by_default"] == {
        "payment_gateway": True,
        "signing_provider": True,
    }


def test_pilot_readiness_rejects_missing_encryption_or_non_pilot_environment():
    report = evaluate_pilot_readiness(
        _settings(ENVIRONMENT="development", LEGAL_DATA_ENCRYPTION_KEY="")
    )

    assert report["ready"] is False
    assert report["required"]["pilot_environment"] is False
    assert report["required"]["legal_data_encryption"] is False
