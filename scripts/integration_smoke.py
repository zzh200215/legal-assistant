"""前后端联调冒烟：以真实登录 token 命中前端使用的 API，校验封装形状 + 状态码。"""
import json
import os
import sys
import httpx

BASE = "http://127.0.0.1:8011"


def _load_env_admin() -> tuple[str, str]:
    from dotenv import dotenv_values
    vals = dotenv_values(os.path.join(os.path.dirname(__file__), "..", ".env"))
    return vals.get("ADMIN_USERNAME", "admin"), vals.get("ADMIN_PASSWORD", "")

results = []

def check(name, resp, expect_envelope=True):
    ok = resp.status_code < 500
    detail = f"{resp.status_code}"
    body = None
    try:
        body = resp.json()
    except Exception:
        pass
    if expect_envelope and body is not None and "success" in body:
        if not isinstance(body.get("success"), bool):
            ok = False; detail += " envelope.success非bool"
        if "data" not in body:
            ok = False; detail += " 缺data键"
    elif body is not None and "success" in body and not expect_envelope:
        pass
    results.append((ok, f"{name}: {detail}", resp.text[:120]))

def main():
    ADMIN_USER, ADMIN_PASS = _load_env_admin()
    # 登录
    login = httpx.post(f"{BASE}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    if login.status_code != 200:
        print("LOGIN FAILED", login.status_code, login.text[:200])
        return 1
    token = login.json()["data"]["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    me = httpx.get(f"{BASE}/api/auth/me", headers=h, timeout=15).json()["data"]
    org_id = me.get("organization_id") or 1
    uid = me.get("id")
    print(f"login ok: {me.get('username')} id={uid} org={org_id} role={me.get('role')}")

    # 前端实际路径清单（method, path）—— param 用登录所得 org/uid
    cases = [
        ("GET", "/api/legal/features"),
        ("GET", "/api/legal/overview"),
        ("GET", "/api/legal/sources"),
        ("GET", "/api/legal/consultations"),
        ("GET", "/api/legal/contract-reviews"),
        ("GET", "/api/legal/drafts"),
        ("GET", "/api/legal/metrics"),
        ("GET", "/api/legal/review-queue"),
        ("GET", "/api/legal/review-stats"),
        ("GET", "/api/legal/document-templates"),
        ("GET", "/api/billing/plans"),
        ("GET", "/api/billing/subscriptions/me"),
        ("GET", "/api/billing/subscriptions/quota"),
        ("GET", "/api/legal/orgs/%d/cases" % org_id),
        ("GET", "/api/legal/orgs/%d/contracts" % org_id),
        ("GET", "/api/legal/orgs/%d/portal-branding" % org_id),
        ("GET", "/api/legal/orgs/%d/portal-feedback" % org_id),
        ("GET", "/api/documents/"),
        ("GET", "/api/documents/knowledge-bases"),
        ("GET", "/api/memory/preferences"),
        ("GET", "/api/connectors/"),
        ("GET", "/api/connectors/sync-jobs"),
        ("GET", "/api/developer/orgs/%d/apps" % org_id),
        ("GET", "/api/developer/orgs/%d/operations/summary" % org_id),
        ("GET", "/api/legal/contracts/expiry-alerts"),
        ("GET", "/api/tasks/"),
        ("GET", "/api/agent/registry"),
        ("GET", "/api/agent/runs"),
        ("GET", "/api/agent/metrics"),
        ("GET", "/api/analytics/tokens/my-stats"),
        ("GET", "/api/analytics/llm-calls/stats"),
        ("GET", "/api/analytics/oplogs"),
        ("GET", "/api/analytics/task-runs"),
        ("GET", "/api/analytics/feedback"),
        ("GET", "/api/analytics/tool-health"),
        ("GET", "/api/admin/funnel"),
        ("GET", "/api/admin/retention"),
        ("GET", "/api/admin/north-star"),
        ("GET", "/api/admin/dashboard"),
    ]
    for method, path in cases:
        url = BASE + path
        try:
            r = httpx.request(method, url, headers=h, timeout=20)
            check(f"{method} {path}", r)
        except Exception as e:
            results.append((False, f"{method} {path}: EXC {e}", ""))

    fails = [r for r in results if not r[0]]
    print(f"\n=== {len(cases)} 个前端路径冒烟: {len(cases)-len(fails)} 通过, {len(fails)} 失败 ===")
    for ok, name, body in results:
        if not ok:
            print(f"  ❌ {name}")
            if body: print(f"     {body}")
        else:
            print(f"  ✅ {name}")
    return 1 if fails else 0

if __name__ == "__main__":
    raise SystemExit(main())
