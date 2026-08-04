"""Gate: diff the current FastAPI OpenAPI schema against the committed snapshot.

Detects contract drift that would silently break the frontend:
  - removed / renamed endpoint paths or HTTP methods
  - removed / renamed / added-required query or path parameters
  - requestBody required flag changes
  - response status code set changes
  - component schema property add/remove or type/required change

Exit codes:
  0  no drift
  1  drift detected (run with --update to rebaseline after an intentional change)
  2  snapshot missing (run scripts/export_openapi.py first)

Use in CI / precheck so a "一改即红" contract gate replaces manual DTO alignment.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/app.db")
os.environ.setdefault("SECRET_KEY", "x" * 64)
os.environ.setdefault("LLM_API_KEY", "sk-" + "a" * 30)
os.environ.setdefault(
    "LEGAL_DATA_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"B" * 32).decode("ascii"),
)
os.environ.setdefault("ENVIRONMENT", "development")
# E-7：契约检查只关心路由 schema，隔离向量库目录避免存量数据 schema 冲突。
os.environ.setdefault("CHROMA_PERSIST_DIR", os.path.join(os.environ.get("TEMP", "/tmp"), "aibg_contract_check_chroma"))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402


def _param_key(param: dict) -> str:
    return f"{param.get('name')}|{param.get('in')}"


def _operation_surface(op: dict) -> dict:
    """Reduce an operation to the contract-relevant surface (ignore descriptions)."""
    params = {}
    for p in op.get("parameters", []):
        params[_param_key(p)] = bool(p.get("required"))
    request_body = op.get("requestBody", {})
    responses = {str(k) for k in op.get("responses", {})}
    return {
        "parameters": params,
        "request_required": bool(request_body.get("required")),
        "responses": sorted(responses),
    }


def _schema_surface(schema: dict) -> dict:
    """Reduce a component schema to property names/types/required (ignore docs)."""
    props = {}
    for name, prop in (schema.get("properties") or {}).items():
        props[name] = {
            "type": prop.get("type"),
            "ref": prop.get("$ref"),
            "enum": prop.get("enum"),
        }
    return {
        "type": schema.get("type"),
        "required": sorted(schema.get("required") or []),
        "properties": props,
    }


def _diff_current_vs_snapshot(current: dict, snapshot: dict) -> list[str]:
    diffs: list[str] = []

    cur_paths = current.get("paths", {})
    snap_paths = snapshot.get("paths", {})

    removed_paths = sorted(set(snap_paths) - set(cur_paths))
    added_paths = sorted(set(cur_paths) - set(snap_paths))
    for p in removed_paths:
        diffs.append(f"endpoint removed: {p}")
    for p in added_paths:
        diffs.append(f"endpoint added: {p}")

    for path in sorted(set(cur_paths) & set(snap_paths)):
        cur_ops, snap_ops = cur_paths[path], snap_paths[path]
        removed_methods = sorted(set(snap_ops) - set(cur_ops))
        added_methods = sorted(set(cur_ops) - set(snap_ops))
        for m in removed_methods:
            diffs.append(f"method removed: {m.upper()} {path}")
        for m in added_methods:
            diffs.append(f"method added: {m.upper()} {path}")

        for method in sorted(set(cur_ops) & set(snap_ops)):
            cur_surf = _operation_surface(cur_ops[method])
            snap_surf = _operation_surface(snap_ops[method])
            if cur_surf != snap_surf:
                diffs.append(f"operation changed: {method.upper()} {path}")

    cur_schemas = current.get("components", {}).get("schemas", {})
    snap_schemas = snapshot.get("components", {}).get("schemas", {})
    removed_schemas = sorted(set(snap_schemas) - set(cur_schemas))
    for s in removed_schemas:
        diffs.append(f"schema removed: {s}")
    for s in sorted(set(cur_schemas) & set(snap_schemas)):
        cur_surface = _schema_surface(cur_schemas[s])
        snap_surface = _schema_surface(snap_schemas[s])
        if cur_surface != snap_surface:
            diffs.append(f"schema changed: {s}")
    return diffs


def main() -> int:
    snapshot_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--")
        else ROOT / "docs" / "openapi-snapshot.json"
    )
    if not snapshot_path.exists():
        print(f"[FAIL] openapi snapshot missing: {snapshot_path}")
        print("Run: python scripts/export_openapi.py")
        return 2

    current = app.openapi()
    if "--update" in sys.argv:
        snapshot_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] snapshot rebaselined: {snapshot_path}")
        return 0

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    diffs = _diff_current_vs_snapshot(current, snapshot)
    if diffs:
        print(f"[FAIL] contract drift detected ({len(diffs)} changes):")
        for d in diffs[:100]:
            print(f"  - {d}")
        if len(diffs) > 100:
            print(f"  ... and {len(diffs) - 100} more")
        print("After an intentional API change, rebaseline with:")
        print("  python scripts/check_openapi_contract.py --update")
        return 1

    paths = len(current.get("paths", {}))
    schemas = len(current.get("components", {}).get("schemas", {}))
    print(f"[OK] no contract drift ({paths} paths, {schemas} schemas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
