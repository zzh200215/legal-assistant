"""法律咨询端到端冒烟：真实登录 + 真实调用 POST /api/legal/consultations。

前置：后端已在 BASE_URL 就绪（默认 http://127.0.0.1:8001）。
用法：
    python scripts/copilot_consult_smoke.py [BASE_URL] [USERNAME] [PASSWORD]
可选环境变量：SMOKE_QUESTION、SMOKE_OUT（完整响应落盘路径，UTF-8）。

输出：登录状态码与耗时、咨询接口状态码与端到端耗时、响应结构（字段名+类型+样例值）、
references[] 每项的完整字段清单。不回显 token 明文，只回显其长度。
"""

import json
import os
import sys
import time

import httpx

DEFAULT_QUESTION = "试用期最长多久？被辞退经济补偿怎么算？"


def _typed(value, depth=0):
    """把 JSON 值压成「类型 + 样例值」，避免整段业务文本刷屏。"""
    if isinstance(value, dict):
        return {k: _typed(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return "list[len=0]"
        return ["list[len=%d] first=%s" % (len(value), json.dumps(_typed(value[0], depth + 1), ensure_ascii=False))]
    if isinstance(value, str):
        return "str(len=%d) %s" % (len(value), value[:80])
    return "%s %r" % (type(value).__name__, value)


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("SMOKE_BASE_URL", "http://127.0.0.1:8001")).rstrip("/")
    username = sys.argv[2] if len(sys.argv) > 2 else os.getenv("SMOKE_USERNAME", "demo_lawyer")
    password = sys.argv[3] if len(sys.argv) > 3 else os.getenv("SMOKE_PASSWORD", "Demo@123456")
    question = os.getenv("SMOKE_QUESTION", DEFAULT_QUESTION)

    out_path = os.getenv("SMOKE_OUT", "")
    result = {"base_url": base, "username": username, "question": question}

    with httpx.Client(timeout=180.0) as client:
        t0 = time.perf_counter()
        login = client.post(f"{base}/api/auth/login", json={"username": username, "password": password})
        login_ms = (time.perf_counter() - t0) * 1000
        result["login"] = {"status_code": login.status_code, "elapsed_ms": round(login_ms, 1)}
        try:
            body = login.json()
        except Exception:
            body = {}
        token = ((body.get("data") or {}).get("access_token")) or body.get("access_token")
        result["login"]["token_present"] = bool(token)
        result["login"]["token_length"] = len(token) if token else 0
        if not token:
            result["login"]["response_keys"] = sorted(body.keys())
            result["conclusion"] = "LOGIN FAILED"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

        payload = {"question": question}
        t0 = time.perf_counter()
        resp = client.post(f"{base}/api/legal/consultations", json=payload,
                           headers={"Authorization": f"Bearer {token}"})
        consult_ms = (time.perf_counter() - t0) * 1000
        result["consultation"] = {
            "request_body_keys": sorted(payload.keys()),
            "status_code": resp.status_code,
            "elapsed_ms": round(consult_ms, 1),
        }
        try:
            data = resp.json()
        except Exception:
            result["consultation"]["non_json_body_head"] = resp.text[:300]
            result["conclusion"] = "CONSULTATION NON-JSON"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

        if resp.status_code != 200:
            result["consultation"]["error_body"] = json.dumps(data, ensure_ascii=False)[:600]
            result["conclusion"] = "CONSULTATION FAILED"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

        result["consultation"]["structure"] = _typed(data)
        envelope = data.get("data") if isinstance(data, dict) else None
        body = envelope if isinstance(envelope, dict) else data
        refs = body.get("references") if isinstance(body, dict) else None
        if isinstance(refs, list) and refs:
            result["consultation"]["references_count"] = len(refs)
            result["consultation"]["references_item_field_inventory"] = [
                {"field": k, "json_type": type(v).__name__,
                 "sample": (json.dumps(v, ensure_ascii=False)[:160] if not isinstance(v, (dict, list))
                            else json.dumps(v, ensure_ascii=False)[:260])}
                for k, v in refs[0].items()
            ]
            result["consultation"]["references_all_item_key_sets"] = [
                sorted(item.keys()) for item in refs if isinstance(item, dict)
            ]
        result["conclusion"] = "OK"

    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            result["full_response_written_to"] = out_path
        except OSError as exc:
            result["full_response_write_failed"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
