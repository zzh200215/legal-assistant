"""Export the FastAPI OpenAPI schema to a snapshot file for contract-diff gating.

The snapshot is committed to the repo; scripts/check_openapi_contract.py diffs the
current schema against it so frontend/backend DTO drift (see V3.0 P0-02) fails the
gate instead of silently diverging.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

# Bootstrap minimal valid settings BEFORE importing the app: the config validator
# rejects empty/placeholder LLM_API_KEY and requires an independent legal-data key.
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/app.db")
os.environ.setdefault("SECRET_KEY", "x" * 64)
os.environ.setdefault("LLM_API_KEY", "sk-" + "a" * 30)
os.environ.setdefault(
    "LEGAL_DATA_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"B" * 32).decode("ascii"),
)
os.environ.setdefault("ENVIRONMENT", "development")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402


def main() -> int:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "openapi-snapshot.json"
    schema = app.openapi()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"openapi schema exported ({len(schema.get('paths', {}))} paths) -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
