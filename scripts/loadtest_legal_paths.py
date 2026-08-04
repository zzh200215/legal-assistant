"""E-7 三条主路径压测基线：咨询 / 审查 / 检索。

在本地 ASGI 进程内启动 FastAPI 应用，mock LLM 返回（不产生真实调用与计费），
对三个生产端点打并发，输出延迟分位数 / 吞吐 / 错误率作为试点基线。

用法:
    python -B scripts/loadtest_legal_paths.py
    python -B scripts/loadtest_legal_paths.py --total 120 --concurrency 20 --llm-delay-ms 300

说明:
    - 默认使用本机 MySQL 库 aibg_loadtest（自动 create_all，不触碰业务库）。
    - --llm-delay-ms 模拟 LLM 往返时延；qwen-plus 实际约 2-5s，这里只测
      应用层（路由 + 鉴权 + DB + 配额）在真实 LLM 响应前的吞吐与排队能力。
"""
import argparse
import asyncio
import base64
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("LLM_API_KEY", "sk-loadtest-" + "a" * 30)
os.environ.setdefault(
    "LEGAL_DATA_ENCRYPTION_KEY",
    base64.urlsafe_b64encode(b"L" * 32).decode("ascii"),
)
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+pymysql://root:123456@localhost:3306/aibg_loadtest",
)
# 压测使用独立向量库目录，避免与业务库旧数据/schema 相互影响。
os.environ.setdefault("CHROMA_PERSIST_DIR", os.path.join(os.environ.get("TEMP", "/tmp"), "aibg_loadtest_chroma"))

from app.core.auth import create_access_token  # noqa: E402
from app.core.database import Base, engine  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.subscription import PlanTier, SubscriptionPlan, UserSubscription  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.llm_service import llm_service  # noqa: E402

BASE = "http://testserver/api/legal"

CONSULTATION_FAKE = {
    "category": "labor_dispute",
    "known_facts": ["公司拖欠工资", "在职期间"],
    "missing_facts": ["入职时间"],
    "advice": "建议整理工资流水与劳动合同，向劳动监察部门投诉或申请劳动仲裁。",
    "risk_level": "low",
    "references": [],
}

REVIEW_FAKE = {
    "risks": [],
    "summary": "未发现高风险条款，需结合合同全文核验。",
}


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * p)))
    return ordered[index]


async def setup_user(db) -> tuple[int, str]:
    """创建压测用户（team 计划，配额无限）并返回 (user_id, auth_header)。"""
    user = db.query(User).filter(User.username == "loadtest").first()
    if not user:
        from app.core.auth import hash_password
        user = User(
            username="loadtest",
            email="loadtest@local.test",
            hashed_password=hash_password("loadtest-pw"),
            role="user",
            status="active",
        )
        db.add(user)
        db.flush()
        db.commit()
        db.refresh(user)

    team_plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.tier == PlanTier.team.value).first()
    if not team_plan:
        team_plan = SubscriptionPlan(
            tier=PlanTier.team.value,
            name="团队版",
            quota_consultation=-1,
            quota_review=-1,
            quota_draft=-1,
        )
        db.add(team_plan)
        db.flush()

    sub = db.query(UserSubscription).filter(
        UserSubscription.user_id == user.id,
        UserSubscription.status == "active",
    ).first()
    if not sub:
        sub = UserSubscription(user_id=user.id, plan_id=team_plan.id, status="active")
        db.add(sub)
    db.commit()

    token = create_access_token({"sub": str(user.id)})
    return user.id, {"Authorization": f"Bearer {token}"}


async def run_load(
    client,
    path: str,
    payload: dict,
    headers: dict,
    total: int,
    concurrency: int,
) -> dict:
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors = 0
    lock = asyncio.Lock()
    wall_started = time.perf_counter()

    async def worker():
        nonlocal errors
        while True:
            async with sem:
                async with lock:
                    if len(latencies) >= total:
                        return
                started = time.perf_counter()
                try:
                    resp = await client.post(path, json=payload, headers=headers)
                    if resp.status_code != 200:
                        errors += 1
                except Exception:
                    errors += 1
                elapsed = (time.perf_counter() - started) * 1000
                async with lock:
                    latencies.append(elapsed)
                    if len(latencies) >= total:
                        return

    await asyncio.gather(*[worker() for _ in range(concurrency)])
    # 吞吐 = 请求数 / 墙钟总耗时（sum(latencies) 是延迟之和，并发下无意义）
    wall = time.perf_counter() - wall_started
    throughput = total / wall
    return {
        "path": path,
        "requests": total,
        "errors": errors,
        "error_rate": round(errors / total, 4),
        "throughput_rps": round(throughput, 2),
        "p50_ms": round(_percentile(latencies, 0.50), 1),
        "p90_ms": round(_percentile(latencies, 0.90), 1),
        "p95_ms": round(_percentile(latencies, 0.95), 1),
        "p99_ms": round(_percentile(latencies, 0.99), 1),
        "max_ms": round(max(latencies), 1),
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="E-7 法律三主路径压测基线")
    parser.add_argument("--total", type=int, default=60, help="每路径请求数")
    parser.add_argument("--concurrency", type=int, default=10, help="并发数")
    parser.add_argument("--llm-delay-ms", type=float, default=150, help="模拟 LLM 往返时延(ms)")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user_id, headers = await setup_user(db)
    finally:
        db.close()

    # 设置 mock LLM 时延；只替换 generate（咨询/审查都经 _llm_chat -> llm_service.generate）
    delay = args.llm_delay_ms / 1000

    async def fake_generate(prompt, temperature=0.3, action=None, user_id=None, **kwargs):
        if delay > 0:
            await asyncio.sleep(delay)
        if action == "legal_consultation":
            return json.dumps(CONSULTATION_FAKE, ensure_ascii=False)
        return json.dumps(REVIEW_FAKE, ensure_ascii=False)

    llm_service.generate = fake_generate  # type: ignore[method-assign]

    import httpx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE, timeout=30
    ) as client:
        scenarios = [
            ("/consultations", {"question": "公司拖欠工资三个月，如何主张权利？"}),
            ("/contract-reviews", {"title": "压测用工合同", "content": "甲乙双方于2026年1月签订劳动合同，约定试用期六个月，工资按月发放。"}),
            ("/sources/retrieval-test", {"question": "劳动仲裁时效是多久？"}),
        ]
        # 预热：创建 demo 法源等一次性成本，不计入基线
        for path, payload in scenarios:
            await client.post(path, json=payload, headers=headers)

        print(f"\nLLM 模拟时延: {args.llm_delay_ms:.0f}ms | 并发: {args.concurrency} | 每路径: {args.total}\n")
        print(f"{'路径':<32}{'RPS':>8}{'p50':>9}{'p90':>9}{'p95':>9}{'p99':>9}{'max':>9}{'err%':>7}")
        print("-" * 92)
        results = []
        for path, payload in scenarios:
            result = await run_load(client, path, payload, headers, args.total, args.concurrency)
            results.append(result)
            print(
                f"{result['path']:<32}{result['throughput_rps']:>8.2f}"
                f"{result['p50_ms']:>9.1f}{result['p90_ms']:>9.1f}"
                f"{result['p95_ms']:>9.1f}{result['p99_ms']:>9.1f}"
                f"{result['max_ms']:>9.1f}{result['error_rate']:>7.2%}"
            )
        return results


if __name__ == "__main__":
    asyncio.run(main())
