"""Gate: diff the current FastAPI OpenAPI schema against the committed snapshot.

P1 升级：在原有漂移检测基础上增加 breaking/non-breaking 分类与显式批准机制：
- breaking（必须 fail，除非在 docs/openapi-breaking-approvals.json 显式批准）：
  - endpoint / method 删除；参数删除或新增必填参数；requestBody required 变化；
    响应状态码集合删除；schema 字段删除 / 必填字段新增 / 类型或枚举变更；
    错误码（x-error-codes）删除。
- non-breaking（放行并打印）：
  - endpoint / method 新增；可选参数新增；响应状态码新增；schema 新字段（非必填）/ 新 schema；
    错误码新增。
- operationId 唯一性检查（重复即 fail）。
- 生成失败 / schema 无法序列化 → fail（异常自然上抛）。

批准文件格式 docs/openapi-breaking-approvals.json：
  {"approvals": [{"kind": "endpoint_removed", "target": "GET /api/x",
                  "reason": "...", "approved_until": "2026-12-31"}]}
kind ∈ endpoint_removed / method_removed / param_removed / param_required_added /
       request_required / response_removed / schema_field_removed /
       schema_required_added / schema_type_changed / error_code_removed
target 为具体对象（路径 / "METHOD path" / 参数名 / schema 字段名 / 错误码）。

Exit codes:
  0  无 breaking drift（non-breaking 变更打印后通过）
  1  breaking drift 未批准（用 --update 重基线 或 加入批准文件）
  2  snapshot missing（运行 python scripts/export_openapi.py）
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import date
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

APPROVAL_FILE = ROOT / "docs" / "openapi-breaking-approvals.json"

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _param_key(param: dict) -> str:
    return f"{param.get('name')}|{param.get('in')}"


def _operation_surface(op: dict) -> dict:
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


def _load_approvals() -> dict:
    if not APPROVAL_FILE.exists():
        return {}
    try:
        data = json.loads(APPROVAL_FILE.read_text(encoding="utf-8"))
    except (TypeError, ValueError):
        print(f"[WARN] 批准文件无法解析: {APPROVAL_FILE}")
        return {}
    today = date.today().isoformat()
    approvals = {}
    for item in data.get("approvals") or []:
        until = item.get("approved_until")
        if until and until < today:
            continue  # 已过期
        approvals[(item.get("kind"), item.get("target"))] = item.get("reason") or ""
    return approvals


def _diff_current_vs_snapshot(current: dict, snapshot: dict) -> tuple[list[str], list[str]]:
    """返回 (breaking, non_breaking) 变更清单。"""
    breaking: list[str] = []
    additive: list[str] = []
    approvals = _load_approvals()

    def report(kind: str, target: str, desc: str) -> None:
        if (kind, target) in approvals:
            additive.append(f"[已批准] {desc}（{approvals[(kind, target)]}）")
        else:
            breaking.append(desc)

    cur_paths = current.get("paths", {})
    snap_paths = snapshot.get("paths", {})

    for p in sorted(set(snap_paths) - set(cur_paths)):
        report("endpoint_removed", p, f"endpoint removed: {p}")
    for p in sorted(set(cur_paths) - set(snap_paths)):
        additive.append(f"endpoint added: {p}")

    for path in sorted(set(cur_paths) & set(snap_paths)):
        cur_ops, snap_ops = cur_paths[path], snap_paths[path]
        for m in sorted(set(snap_ops) - set(cur_ops)):
            report("method_removed", f"{m.upper()} {path}", f"method removed: {m.upper()} {path}")
        for m in sorted(set(cur_ops) - set(snap_ops)):
            additive.append(f"method added: {m.upper()} {path}")

        for method in sorted(set(cur_ops) & set(snap_ops)):
            cur_surf = _operation_surface(cur_ops[method])
            snap_surf = _operation_surface(snap_ops[method])
            label = f"{method.upper()} {path}"
            for pkey in set(snap_surf["parameters"]) - set(cur_surf["parameters"]):
                report("param_removed", f"{label} {pkey}", f"parameter removed: {label} {pkey}")
            for pkey in set(cur_surf["parameters"]) - set(snap_surf["parameters"]):
                additive.append(f"parameter added: {label} {pkey}")
            for pkey in set(cur_surf["parameters"]) & set(snap_surf["parameters"]):
                if cur_surf["parameters"][pkey] and not snap_surf["parameters"][pkey]:
                    report("param_required_added", f"{label} {pkey}",
                           f"parameter became required: {label} {pkey}")
            if cur_surf["request_required"] and not snap_surf["request_required"]:
                report("request_required", label, f"requestBody became required: {label}")
            removed_resp = set(snap_surf["responses"]) - set(cur_surf["responses"])
            for code in sorted(removed_resp):
                report("response_removed", f"{label} {code}", f"response removed: {label} {code}")
            for code in sorted(set(cur_surf["responses"]) - set(snap_surf["responses"])):
                additive.append(f"response added: {label} {code}")

    cur_schemas = current.get("components", {}).get("schemas", {})
    snap_schemas = snapshot.get("components", {}).get("schemas", {})
    for s in sorted(set(snap_schemas) - set(cur_schemas)):
        report("schema_removed", s, f"schema removed: {s}")
    for s in sorted(set(cur_schemas) - set(snap_schemas)):
        additive.append(f"schema added: {s}")
    for s in sorted(set(cur_schemas) & set(snap_schemas)):
        cur_surface = _schema_surface(cur_schemas[s])
        snap_surface = _schema_surface(snap_schemas[s])
        for prop in set(snap_surface["properties"]) - set(cur_surface["properties"]):
            report("schema_field_removed", f"{s}.{prop}", f"schema field removed: {s}.{prop}")
        for prop in set(cur_surface["properties"]) - set(snap_surface["properties"]):
            additive.append(f"schema field added: {s}.{prop}")
        if cur_surface["type"] != snap_surface["type"]:
            report("schema_type_changed", s, f"schema type changed: {s}")
        for prop in set(cur_surface["properties"]) & set(snap_surface["properties"]):
            cur_p, snap_p = cur_surface["properties"][prop], snap_surface["properties"][prop]
            if cur_p["type"] != snap_p["type"] or cur_p["ref"] != snap_p["ref"]:
                report("schema_type_changed", f"{s}.{prop}", f"schema field type changed: {s}.{prop}")
            if cur_p["enum"] != snap_p["enum"]:
                report("schema_type_changed", f"{s}.{prop}", f"schema field enum changed: {s}.{prop}")
        for req in set(cur_surface["required"]) - set(snap_surface["required"]):
            report("schema_required_added", f"{s}.{req}", f"schema field became required: {s}.{req}")
        for req in set(snap_surface["required"]) - set(cur_surface["required"]):
            additive.append(f"schema field no longer required: {s}.{req}")

    cur_codes = set(current.get("x-error-codes") or [])
    snap_codes = set(snapshot.get("x-error-codes") or [])
    for code in sorted(snap_codes - cur_codes):
        report("error_code_removed", code, f"error code removed: {code}")
    for code in sorted(cur_codes - snap_codes):
        additive.append(f"error code added: {code}")

    return breaking, additive


def _check_operation_id_uniqueness(schema: dict) -> list[str]:
    seen: dict[str, str] = {}
    problems: list[str] = []
    for path, methods in schema.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            opid = op.get("operationId")
            if not opid:
                problems.append(f"missing operationId: {method.upper()} {path}")
                continue
            if opid in seen:
                problems.append(
                    f"duplicate operationId {opid!r}: {seen[opid]} and {method.upper()} {path}"
                )
            else:
                seen[opid] = f"{method.upper()} {path}"
    return problems


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
        from scripts.export_openapi import main as export_main  # noqa: PLC0415
        raise SystemExit(export_main())

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    op_problems = _check_operation_id_uniqueness(current)
    if op_problems:
        print(f"[FAIL] operationId 契约问题（{len(op_problems)} 处）:")
        for p in op_problems[:50]:
            print(f"  - {p}")
        return 1

    breaking, additive = _diff_current_vs_snapshot(current, snapshot)
    for item in additive:
        print(f"[NON-BREAKING] {item}")
    if breaking:
        print(f"[FAIL] breaking change detected ({len(breaking)} 项，需显式批准):")
        for b in breaking[:100]:
            print(f"  - {b}")
        if len(breaking) > 100:
            print(f"  ... and {len(breaking) - 100} more")
        print("处理方式（二选一）:")
        print("  1) 加入 docs/openapi-breaking-approvals.json（kind/target/reason/approved_until）")
        print("  2) 确认变更后重基线: python scripts/check_openapi_contract.py --update")
        return 1

    paths = len(current.get("paths", {}))
    schemas = len(current.get("components", {}).get("schemas", {}))
    codes = len(current.get("x-error-codes") or [])
    print(f"[OK] no breaking drift ({paths} paths, {schemas} schemas, {codes} error codes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
