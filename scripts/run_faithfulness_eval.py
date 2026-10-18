"""忠实度真模型评测：对已有真实答案逐题跑 check_faithfulness，产出忠实度指标。

不重跑检索与生成：直接吃 `eval/outputs/qa_eval_repaired.json` 里已生成的答案与引用片段
（`citations[].source_text` 就是当时的证据块），只把「答案 vs 证据」这一步交给真模型，
因此成本只有每题的裁判往返。

用法：
  $env:PYTHONPATH="<根>"
  python -B scripts/run_faithfulness_eval.py --result eval/outputs/qa_eval_repaired.json \
      --output faithfulness_report.json --min-interval 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.services.rag.agentic_judge import check_faithfulness


def lift_llm_limits() -> None:
    settings = get_settings()
    settings.LLM_DAILY_REQUEST_LIMIT = 1_000_000
    settings.LLM_DAILY_TOKEN_LIMIT = 1_000_000_000
    settings.LLM_RATE_LIMIT_MAX_REQUESTS = 1_000_000
    settings.LLM_RATE_LIMIT_CONFIG_JSON = "{}"
    settings.LLM_BUDGET_LIMITS_JSON = "{}"


async def run(args: argparse.Namespace) -> dict:
    result = json.loads((ROOT / args.result).read_text(encoding="utf-8"))
    cases = [case for case in result["cases"] if not case.get("should_refuse") and case.get("can_answer")]
    delays = [float(item) for item in args.backoff.split(",") if item.strip()]
    state = {"last": 0.0}
    rows: list[dict] = []
    for index, case in enumerate(cases, start=1):
        chunks = [
            {"content": citation.get("source_text") or "", "metadata": {"document_id": citation.get("document_id")}}
            for citation in case.get("citations") or []
            if citation.get("source_text")
        ]
        elapsed = time.monotonic() - state["last"]
        if elapsed < args.min_interval:
            await asyncio.sleep(args.min_interval - elapsed)
        judgement = None
        for attempt in range(len(delays) + 1):
            try:
                judgement = await check_faithfulness(case["question"], case.get("answer") or "", chunks)
                state["last"] = time.monotonic()
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 429 or attempt == len(delays):
                    raise
                await asyncio.sleep(delays[attempt])
        rows.append(
            {
                "name": case.get("name"),
                "evidence_chunks": len(chunks),
                "available": bool(judgement and judgement.get("available")),
                "faithful": (judgement or {}).get("faithful"),
                "unsupported": (judgement or {}).get("unsupported") or [],
                "reason": (judgement or {}).get("reason"),
            }
        )
        print(f"[{index}/{len(cases)}] {case.get('name')} faithful={rows[-1]['faithful']}", flush=True)

    judged = [row for row in rows if row["available"]]
    faithful = [row for row in judged if row["faithful"] is True]
    summary = {
        "cases": len(rows),
        "judged": len(judged),
        "unavailable": len(rows) - len(judged),
        "faithful_cases": len(faithful),
        "faithfulness_rate": round(len(faithful) / len(judged), 4) if judged else None,
        "cases_with_unsupported_claims": sum(1 for row in judged if row["unsupported"]),
        "unsupported_claims_total": sum(len(row["unsupported"]) for row in judged),
    }
    return {
        "eval": "faithfulness_real_model",
        "source_result": args.result,
        "judge_model": get_settings().LLM_MODEL,
        "summary": summary,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", default="eval/outputs/qa_eval_repaired.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--min-interval", type=float, default=10.0)
    parser.add_argument("--backoff", default="30,60,90,120,180")
    args = parser.parse_args()

    lift_llm_limits()
    report = asyncio.run(run(args))
    (ROOT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
