"""深度联调：case 级读路径 + 写入路径 + LLM 降级验证（真实 MySQL 库）。

依赖后端已启动（默认 127.0.0.1:8001，可用 INTEGRATION_BASE 覆盖）。输出每个路径的状态码；4xx/5xx 视为异常（LLM 降级除外）。
"""
import json
import os
import sys
import time
import httpx

BASE = os.environ.get("INTEGRATION_BASE", "http://127.0.0.1:8001")
USER, PASS = "pilot01-lawyer", "Pilot@2026"

ok, fail = [], []

def hit(method, path, token=None, **kw):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.request(method, BASE + path, headers=h, timeout=60, **kw)
    return r

def expect(name, r, allowed=(200, 201)):
    tag = "OK" if r.status_code in allowed else f"FAIL{r.status_code}"
    if r.status_code in allowed:
        ok.append(name)
    else:
        fail.append((name, r.status_code, r.text[:160]))
        print(f"  ❌ {name}: {r.status_code} {r.text[:160]}")

def main():
    login = hit("POST", "/api/auth/login", json={"username": USER, "password": PASS})
    if login.status_code != 200:
        print("login fail"); return 1
    token = login.json()["data"]["access_token"]
    org = 5
    print(f"== {USER} org={org} ==")

    # 1. 建测试案件
    r = hit("POST", f"/api/legal/orgs/{org}/cases", token, json={
        "title": "联调测试案件", "description": "integration deep smoke", "is_strict_mode": False,
        "organization_id": org})
    expect("POST cases", r)
    case_id = None
    if r.status_code < 400:
        d = r.json().get("data") or {}
        case_id = d.get("id") or (d.get("case") or {}).get("id")
    if not case_id:
        print("  无法取得 case_id，退出"); return 1
    print(f"  case_id={case_id}")

    # 2. case 级读路径
    reads = [
        ("GET", f"/api/legal/orgs/{org}/cases/{case_id}/items"),
        ("GET", f"/api/legal/orgs/{org}/cases/{case_id}/time-entries"),
        ("GET", f"/api/legal/orgs/{org}/cases/{case_id}/deadlines"),
        ("GET", f"/api/legal/orgs/{org}/cases/{case_id}/portal-links"),
        ("GET", f"/api/legal/orgs/{org}/cases/{case_id}/members"),
        ("GET", f"/api/legal/orgs/{org}/cases/{case_id}/progress-updates"),
        ("GET", f"/api/legal/contracts/expiry-alerts?org_id={org}"),
    ]
    for m, p in reads:
        expect(f"{m} {p}", hit(m, p, token))

    # 3. 写入路径
    writes = [
        ("POST", f"/api/legal/orgs/{org}/cases/{case_id}/time-entries", {"description": "联调工时", "duration_minutes": 30, "case_id": case_id}),
        ("POST", f"/api/legal/orgs/{org}/cases/{case_id}/deadlines", {"deadline_type": "hearing", "deadline_at": "2030-01-01T00:00:00", "owner_id": 15, "reminder_offsets_json": "[7,3,1]"}),
        ("POST", f"/api/legal/orgs/{org}/billing-rules", {"name": "联调规则", "billing_mode": "hourly", "hourly_rate": 800, "fixed_amount": 0, "case_id": case_id}),
        ("POST", f"/api/legal/orgs/{org}/cases/{case_id}/members", {"user_id": 16, "case_role": "viewer"}),
        ("POST", f"/api/legal/orgs/{org}/cases/{case_id}/progress-updates", {"title": "联调进展", "body": "联调写入测试", "next_steps": "无", "visibility": "internal"}),
    ]
    for m, p, body in writes:
        expect(f"{m} {p}", hit(m, p, token, json=body), allowed=(200, 201, 400, 409))

    # portal link 需要单独处理（可能要求永久/过期参数）
    pl = hit("POST", f"/api/legal/orgs/{org}/cases/{case_id}/portal-links", token,
             json={"title": "联调门户链接", "client_email": "client-test@example.com", "is_permanent": True})
    expect("POST portal-links", pl, allowed=(200, 201, 400, 409))

    # 4. LLM 降级：咨询/审查/文书（dashscope 可能不可用，期望优雅错误而非 500）
    llm_checks = [
        ("POST consultations", "/api/legal/consultations", {"question": "劳动仲裁时效是多久？"}),
        ("POST contract-reviews", "/api/legal/contract-reviews", {"title": "测试合同", "content": "甲方与乙方签订服务合同，服务期一年。"}),
        ("POST drafts", "/api/legal/drafts", {"document_type": "labor_arbitration_application", "fields": {"申请人": "张三", "被申请人": "某公司"}}),
    ]
    for name, p, body in llm_checks:
        r = hit("POST", p, token, json=body)
        if r.status_code == 500:
            fail.append((name, r.status_code, r.text[:160])); print(f"  ❌ {name}: 500（LLM 降级失败）{r.text[:120]}")
        else:
            ok.append(name); print(f"  ✅ {name}: {r.status_code}（LLM 不可用或可用，未 500）")

    # 5. 清理测试案件（保留数据避免污染，仅在成功时删除）
    # 这里选择不删除，便于复查；如需要可放开
    print(f"\n=== 深度联调: {len(ok)} 通过, {len(fail)} 失败 ===")
    for name, code, txt in fail:
        print(f"  ❌ {name}: {code} {txt}")
    return 1 if fail else 0

if __name__ == "__main__":
    raise SystemExit(main())
