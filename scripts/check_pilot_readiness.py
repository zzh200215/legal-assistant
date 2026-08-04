"""小团队试点环境门禁，不检查或开启外部供应商能力。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings


def evaluate_pilot_readiness(settings: Any) -> dict[str, Any]:
    """Return deterministic pilot checks so they can be verified without infrastructure."""
    environment = str(getattr(settings, "ENVIRONMENT", "")).lower()
    database_url = str(getattr(settings, "DATABASE_URL", ""))
    required = {
        "pilot_environment": environment in {"pilot", "production", "prod"},
        "mysql_or_postgresql": database_url.startswith(("mysql", "postgresql")),
        "redis": bool(str(getattr(settings, "REDIS_URL", "")).strip()),
        "legal_data_encryption": bool(
            str(getattr(settings, "LEGAL_DATA_ENCRYPTION_KEY", "")).strip()
            or str(getattr(settings, "LEGAL_DATA_ENCRYPTION_KEYS_JSON", "")).strip()
        ),
        "legal_data_encryption_independent": bool(
            str(getattr(settings, "LEGAL_DATA_ENCRYPTION_KEY", "")).strip()
            and str(getattr(settings, "LEGAL_DATA_ENCRYPTION_KEY", "")).strip()
            != str(getattr(settings, "SECRET_KEY", "")).strip()
        ),
        "administrator": bool(
            str(getattr(settings, "ADMIN_USERNAME", "")).strip()
            and str(getattr(settings, "ADMIN_PASSWORD", "")).strip()
        ),
        "llm_provider": bool(str(getattr(settings, "LLM_API_KEY", "")).strip()),
    }
    optional = {
        "neo4j_graph_rag": bool(getattr(settings, "NEO4J_ENABLED", False)),
        "outbound_email": bool(
            str(getattr(settings, "SMTP_HOST", "")).strip()
            and str(getattr(settings, "SMTP_USERNAME", "")).strip()
            and str(getattr(settings, "SMTP_PASSWORD", "")).strip()
        ),
    }
    disabled_by_default = {
        "payment_gateway": not bool(str(getattr(settings, "PAYMENT_CHECKOUT_BASE_URL", "")).strip()),
        "signing_provider": not bool(
            str(getattr(settings, "SIGNING_FADADA_SANDBOX_URL", "")).strip()
            or str(getattr(settings, "SIGNING_FADADA_API_KEY", "")).strip()
        ),
    }
    return {
        "ready": all(required.values()),
        "required": required,
        "optional": optional,
        "disabled_by_default": disabled_by_default,
    }


def main() -> int:
    result = evaluate_pilot_readiness(get_settings())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
