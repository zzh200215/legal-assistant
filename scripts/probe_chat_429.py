"""探 chat 生成的限流形态：连发 N 次，报第一次 429 的响应头与响应体。

用法：python -B scripts/probe_chat_429.py [--burst 12] [--interval 2]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from app.core.config import get_settings
from app.core.llm_client import llm_client

INTERESTING = ("retry-after", "x-ratelimit", "ratelimit", "quota", "remaining", "limit", "reset")


def lift() -> None:
    settings = get_settings()
    settings.LLM_DAILY_REQUEST_LIMIT = 1_000_000
    settings.LLM_DAILY_TOKEN_LIMIT = 1_000_000_000
    settings.LLM_RATE_LIMIT_MAX_REQUESTS = 1_000_000
    settings.LLM_RATE_LIMIT_CONFIG_JSON = "{}"
    settings.LLM_BUDGET_LIMITS_JSON = "{}"
    settings.LLM_PRIMARY_REQUEST_RETRIES = 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--burst", type=int, default=12)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    lift()
    settings = get_settings()
    print(f"chat model={settings.LLM_MODEL} base={settings.LLM_API_BASE_URL} burst={args.burst} interval={args.interval}s")
    started = time.monotonic()
    for index in range(1, args.burst + 1):
        try:
            text = await llm_client.generate("只回复两个字：收到", temperature=0.0)
            print(f"  #{index} ok  {time.monotonic() - started:6.1f}s {text.strip()[:12]!r}", flush=True)
        except httpx.HTTPStatusError as exc:
            response = exc.response
            print(f"  #{index} HTTP {response.status_code} at {time.monotonic() - started:.1f}s", flush=True)
            for name, value in response.headers.items():
                if any(token in name.lower() for token in INTERESTING):
                    print(f"    header {name}: {value}")
            print("    body:", response.text[:600].replace("\n", " "))
            return 2
        if index < args.burst:
            await asyncio.sleep(args.interval)
    print(f"no 429 in {args.burst} calls over {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
