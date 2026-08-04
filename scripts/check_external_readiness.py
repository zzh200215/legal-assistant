"""V3 外部前置门禁：仅根据真实配置判断能力是否可开放。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings

def main():
    s = get_settings()
    checks = {
        "EXT-01_signing": bool(s.SIGNING_FADADA_SANDBOX_URL and s.SIGNING_FADADA_API_KEY and s.SIGNING_WEBHOOK_SECRETS_JSON),
        "EXT-02_outbound_email": bool(getattr(s, "SMTP_HOST", "") and getattr(s, "SMTP_USERNAME", "") and getattr(s, "SMTP_PASSWORD", "")),
        "EXT-03_payment": bool(s.PAYMENT_CHECKOUT_BASE_URL),
        "EXT-04_feishu": bool(getattr(s, "FEISHU_APP_ID", "") and getattr(s, "FEISHU_APP_SECRET", "")),
        "EXT-05_production": bool(s.ENVIRONMENT.lower() in ("production", "prod") and (s.LEGAL_DATA_ENCRYPTION_KEY or s.LEGAL_DATA_ENCRYPTION_KEYS_JSON)),
    }
    print(json.dumps({"ready": all(checks.values()), "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 2

if __name__ == "__main__":
    raise SystemExit(main())
