"""性能烟测（阶段 5 CI 强化）：对 3 组关键入口做冒烟级延迟阈值断言。

- 端点：/（根）、/api/health/live（进程存活）、/api/health/ready（就绪探针）。
- 手段：httpx.TestClient（进程内 ASGI，不起真实服务、不依赖外部服务）。
- 阈值：宽松（如实测明显回退 >3s 才失败），仅防明显退化，不做基准。

用法：python -B scripts/perf_smoke.py [--threshold-ms 3000]
退出码：0=通过；1=任一入口超阈值；2=应用启动/用法错误。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

THRESHOLD_MS_DEFAULT = 3000
ENDPOINTS = [
    ("root", "GET", "/"),
    ("liveness", "GET", "/api/health/live"),
    ("readiness", "GET", "/api/health/ready"),
]


def measure() -> list[dict]:
    from fastapi.testclient import TestClient

    from app.main import app

    results = []
    with TestClient(app, raise_server_exceptions=False) as client:
        for name, method, path in ENDPOINTS:
            started = time.perf_counter()
            request = getattr(client, method.lower())
            response = request(path)
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            results.append({
                "name": name, "path": path, "status_code": response.status_code,
                "latency_ms": latency_ms,
            })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Performance smoke gate for key API entrypoints")
    parser.add_argument("--threshold-ms", type=int, default=THRESHOLD_MS_DEFAULT)
    args = parser.parse_args()

    try:
        results = measure()
    except Exception as exc:  # noqa: BLE001 - 应用启动失败即为门禁失败
        print(json.dumps({"status": "error", "message": f"application boot failed: {type(exc).__name__}: {exc}"}))
        return 2

    failures = [r for r in results if r["latency_ms"] > args.threshold_ms]
    payload = {"status": "fail" if failures else "ok", "threshold_ms": args.threshold_ms, "endpoints": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
