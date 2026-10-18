"""跑 32 题样例 QA 评测：真实嵌入 + 真实生成 + 真实裁判。

带三个环境绕行（都不改仓库语义，只为让评测在本机能跑完）：
1. `CHROMA_PERSIST_DIR` 指到工作区根下的新目录。默认的 `./data/chroma_db` 里
   HNSW 段目录缺失，而子进程无法在该目录下建目录（WinError 5），dense 召回会静默
   退化为 0 条（`app/services/rag/retrieval.py:91`）。用文件里的真实索引须换目录。
2. 每个 case 前后清空 `LLMClient._clients`。`eval/run_eval.py:279` 调的是同步
   `agentic_rag_service.answer()`，内部 `run_async` 每个 case 新起一个事件循环，
   而客户端缓存与事件循环无关 —— 复用会抛 `RuntimeError: Event loop is closed`
   （实测第二题即崩）。uvicorn 里走单循环 `answer_async`，不存在这个边界。
3. 生成调用排队 + 429 退避。供应商（sensenova token plan）实测 **7 次/分钟** 后
   返回 `{"error":{"message":"rpm exhausted","type":"quota_exceeded_error"}}`，
   而仓库自带退避只有 `1.5 * attempt` 秒（`app/core/llm_client.py:33,500`），
   32 题连发必崩。用 `--min-interval`（默认 3s，实测须 ≥10s）与 `--backoff` 控速。

**`--output` 必须给工作区根目录下的路径**：python 子进程无法在已存在的子目录里新建
文件（`PermissionError: [Errno 13]`，本机沙箱行为），eval/outputs/ 下写不进去。
落盘后再用 pwsh（父进程）Move-Item 归位。

用法：
  $env:CHROMA_PERSIST_DIR="<根>/chroma_eval_store"
  python -B scripts/run_qa_eval_real.py --dataset eval/qa_dataset.json --top-k 5 \
      --min-interval 10 --output qa_eval_real_generation_judge.json
  Move-Item qa_eval_real_generation_judge.json eval/outputs/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import eval.run_eval as run_eval_module
from app.core.config import get_settings
from app.core.llm_client import llm_client
from app.services.rag.rag_service import rag_service


def lift_llm_limits() -> None:
    settings = get_settings()
    settings.LLM_DAILY_REQUEST_LIMIT = 1_000_000
    settings.LLM_DAILY_TOKEN_LIMIT = 1_000_000_000
    settings.LLM_RATE_LIMIT_MAX_REQUESTS = 1_000_000
    settings.LLM_RATE_LIMIT_CONFIG_JSON = '{"embedding": {"window_seconds": 60, "max_requests": 1000000}}'
    settings.LLM_BUDGET_LIMITS_JSON = '{"embedding": {"daily_requests": 1000000, "daily_tokens": 1000000000}}'


def patch_answer_for_fresh_clients() -> object:
    service = run_eval_module.agentic_rag_service
    original = service.answer

    def answer_with_fresh_clients(*args, **kwargs):
        llm_client._clients.clear()
        try:
            payload = original(*args, **kwargs)
        finally:
            llm_client._clients.clear()
        _record_partial(args[0] if args else kwargs.get("question"), payload)
        return payload

    service.answer = answer_with_fresh_clients
    return service


_PARTIAL_PATH: Path | None = None
_PARTIAL_CASES: list[dict] = []


def _record_partial(question, payload) -> None:
    if _PARTIAL_PATH is None:
        return
    _PARTIAL_CASES.append({"question": question, "answer": payload})
    try:
        _PARTIAL_PATH.write_text(
            json.dumps({"completed_cases": len(_PARTIAL_CASES), "cases": _PARTIAL_CASES}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:  # 进度落盘不该拖垮整个 run
        print(f"[partial] 落盘失败：{exc}", flush=True)


def patch_generate_with_backoff(min_interval: float, delays: list[float], state: dict) -> None:
    """走真模型时给生成调用排队 + 429 退避。

    供应商侧（sensenova token plan）对 chat/completions 有分钟级限流，而仓库自带的重试
    退避只有 `RETRY_BACKOFF_SECONDS = 1.5 * attempt`（`app/core/llm_client.py:33,500`），
    实测不足以穿过窗口，32 题连发会在第二题左右直接 429 崩掉整个 run。
    """
    original = llm_client.generate

    async def generate_with_backoff(prompt, *args, **kwargs):
        elapsed = time.monotonic() - state["last_call"]
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        for attempt in range(len(delays) + 1):
            try:
                result = await original(prompt, *args, **kwargs)
                state["last_call"] = time.monotonic()
                return result
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 429 or attempt == len(delays):
                    raise
                wait = delays[attempt]
                print(f"[429] retry in {wait:.0f}s (attempt {attempt + 1})", flush=True)
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")

    llm_client.generate = generate_with_backoff


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="eval/qa_dataset.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", required=True)
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--min-interval", type=float, default=3.0, help="两次 chat 生成之间的最小间隔秒数")
    parser.add_argument("--backoff", default="20,40,60,90,120", help="429 退避秒数，逗号分隔")
    args = parser.parse_args()

    global _PARTIAL_PATH
    _PARTIAL_PATH = Path(f"{args.output}.partial.json")
    lift_llm_limits()
    settings = get_settings()
    dataset = run_eval_module.load_dataset(ROOT / args.dataset)
    errors = run_eval_module.validate_dataset(dataset)
    if errors:
        raise SystemExit("invalid dataset: " + "; ".join(errors[:5]))

    patch_generate_with_backoff(
        args.min_interval,
        [float(item) for item in args.backoff.split(",") if item.strip()],
        {"last_call": 0.0},
    )
    # 空向量库会让检索恒为空、指标恒 0，白跑一趟；先卡住。
    chunk_count = rag_service.collection.collection.count()
    if not chunk_count:
        raise SystemExit(
            f"向量库为空（CHROMA_PERSIST_DIR={settings.CHROMA_PERSIST_DIR}）："
            "先跑 scripts/index_eval_fixtures.py 把三篇 fixture 索引进去，并确保评测与索引用同一个目录。"
        )
    print(f"[eval] CHROMA_PERSIST_DIR={settings.CHROMA_PERSIST_DIR} chunks={chunk_count}", flush=True)
    patch_answer_for_fresh_clients()
    started = time.perf_counter()
    result = run_eval_module.run_eval(
        dataset,
        user_id=None,
        top_k=args.top_k,
        confidence_threshold=0.35,
        bundle_meta=None,
    )
    result["config"]["chroma_persist_dir"] = settings.CHROMA_PERSIST_DIR
    result["config"]["embedding_model"] = settings.EMBEDDING_MODEL
    result["config"]["generation_elapsed_seconds"] = round(time.perf_counter() - started, 1)

    if not args.skip_judge:
        from eval.llm_judge import judge_cases, summarize_judgements

        async def judge() -> None:
            judgements = await judge_cases(result["cases"])
            for case, judgement in zip(result["cases"], judgements, strict=False):
                case["llm_judge"] = judgement
            result["summary"]["llm_judge"] = summarize_judgements(judgements)

        asyncio.run(judge())
        result["config"]["llm_judge_enabled"] = True
    result["config"]["total_elapsed_seconds"] = round(time.perf_counter() - started, 1)

    run_eval_module.write_eval_output(Path(args.output), result)
    print(json.dumps({"config": result["config"], "summary": result["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
