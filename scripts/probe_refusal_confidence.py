"""复现「检索到 rank-1 证据却拒答」：打印 confidence 的四个分量与最终裁决。

对照三题：
1. 旧题（原型与接口联调）—— 词面不在文档里，是失效标签的题面；
2. 新题（合同审查与法律咨询核心功能）—— 词面在文档里，修复后的题面；
3. 拒答题（预算总额）—— 文档确实没有。

用法（必须先有向量库）：
  $env:CHROMA_PERSIST_DIR="<根>/chroma_eval_store"
  python -B scripts/probe_refusal_confidence.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.services.rag.rag_service import rag_service

QUESTIONS = [
    ("旧题/词面不在文档", "核心页面原型与接口联调的计划完成时间是什么时候？"),
    ("新题/词面在文档", "合同审查与法律咨询核心功能的计划完成时间是什么时候？"),
    ("拒答题/文档无此事", "这个项目的预算总额是多少？"),
]


def lift_llm_limits() -> None:
    settings = get_settings()
    settings.LLM_DAILY_REQUEST_LIMIT = 1_000_000
    settings.LLM_DAILY_TOKEN_LIMIT = 1_000_000_000
    settings.LLM_RATE_LIMIT_MAX_REQUESTS = 1_000_000
    settings.LLM_RATE_LIMIT_CONFIG_JSON = "{}"
    settings.LLM_BUDGET_LIMITS_JSON = "{}"


async def main() -> int:
    lift_llm_limits()
    settings = get_settings()
    cfg = rag_service.get_runtime_config(top_k=5, confidence_threshold=0.35)
    print(
        json.dumps(
            {
                "chroma": settings.CHROMA_PERSIST_DIR,
                "top_k": cfg["top_k"],
                "threshold": cfg["confidence_threshold"],
                "context_max_chunks": cfg["context_max_chunks"],
            },
            ensure_ascii=False,
        )
    )
    for label, question in QUESTIONS:
        try:
            chunks = await rag_service.search_async(question, top_k=cfg["top_k"])
        except TypeError:
            chunks = await rag_service.search_async(question)
        keyword = rag_service._keyword_overlap_score(question, chunks)
        distances = [rag_service._distance_score(chunk.get("distance")) for chunk in chunks[:3]]
        distance = sum(distances) / len(distances) if distances else 0.0
        margin = rag_service._top_score_margin(chunks)
        route = rag_service._route_consistency(chunks)
        confidence = rag_service._estimate_confidence(question, chunks)
        print(f"\n[{label}] chunks={len(chunks)} confidence={confidence:.4f} refuse={confidence < cfg['confidence_threshold']}")
        print(
            f"  keyword={keyword:.3f}*0.45  distance={distance:.3f}*0.30  margin={margin:.3f}*0.15  route={route:.3f}*0.10"
        )
        print("  distances[:3] =", [chunk.get("distance") for chunk in chunks[:3]])
        print("  routes[:3] =", [chunk.get("retrieval_routes") or chunk.get("routes") for chunk in chunks[:3]])
        for chunk in chunks[:2]:
            print("  top:", (chunk.get("content") or "")[:64].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
