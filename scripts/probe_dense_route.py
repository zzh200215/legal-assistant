"""探针：向量（dense）召回在当前环境是否可用，并把持久化目录换到可写位置验证。

背景：Chroma 本地 HNSW 段目录缺失时 `PersistentClient` 会在既有目录下
`os.makedirs(<collection-uuid>)`；本机该目录对子进程不可写（WinError 5），
dense 召回会静默退化为 0 条（`app/services/rag/retrieval.py:91` 只打 warning）。
工作区根目录下的新目录 python 可以创建，所以把 `CHROMA_PERSIST_DIR` 指到根下验证。

运行（真实嵌入，进程内抬限流）：
  python -B scripts/probe_dense_route.py            # 现状目录
  python -B scripts/probe_dense_route.py --seed     # 先写探针片段再查询
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.core.llm_client import llm_client
from app.services.rag.rag_service import rag_service

PROBE_DOC_ID = 8888
PROBE_USER_ID = 1
PROBE_TEXTS = [
    "劳动合同期限三个月以上不满一年的，试用期不得超过一个月。",
    "经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资。",
    "劳动者提前三十日以书面形式通知用人单位，可以解除劳动合同。",
]
PROBE_QUERY = "试用期最长多久"


def lift_llm_limits() -> None:
    settings = get_settings()
    settings.LLM_DAILY_REQUEST_LIMIT = 1_000_000
    settings.LLM_DAILY_TOKEN_LIMIT = 1_000_000_000
    settings.LLM_RATE_LIMIT_MAX_REQUESTS = 1_000_000
    settings.LLM_RATE_LIMIT_CONFIG_JSON = '{"embedding": {"window_seconds": 60, "max_requests": 1000000}}'
    settings.LLM_BUDGET_LIMITS_JSON = '{"embedding": {"daily_requests": 1000000, "daily_tokens": 1000000000}}'


def seed_probe_chunks() -> dict:
    chunks = [
        {
            "id": None,
            "chunk_index": index,
            "content": text,
            "embedding_id": f"probe_doc{PROBE_DOC_ID}_chunk{index}",
            "page_number": None,
            "section_title": None,
            "section_path": None,
            "segment_type": None,
            "table_like": False,
        }
        for index, text in enumerate(PROBE_TEXTS)
    ]
    rag_service.index_document(PROBE_DOC_ID, chunks, user_id=PROBE_USER_ID)
    return {"indexed_chunks": len(chunks)}


async def run(args: argparse.Namespace) -> dict:
    settings = get_settings()
    report: dict = {
        "chroma_persist_dir": settings.CHROMA_PERSIST_DIR,
        "vector_store_provider": settings.VECTOR_STORE_PROVIDER,
        "collection": settings.VECTOR_STORE_COLLECTION_NAME,
    }
    if args.seed:
        try:
            report["seed"] = seed_probe_chunks()
        except Exception as exc:
            report["seed"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        report["collection_count"] = rag_service.collection.collection.count()
    except Exception as exc:
        report["collection_count"] = f"{type(exc).__name__}: {exc}"

    try:
        embedding = (await llm_client.embed([PROBE_QUERY], user_id=PROBE_USER_ID, action="probe_query_embed"))[0]
        raw = await asyncio.to_thread(
            rag_service.collection.query, query_embeddings=[embedding], n_results=3, where=None
        )
        ids = (raw.get("ids") or [[]])[0]
        report["raw_dense_query"] = {"ok": True, "ids": [str(item) for item in ids]}
    except Exception as exc:
        report["raw_dense_query"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        candidates = await rag_service._dense_multi_recall(
            [PROBE_QUERY], where=None, candidate_limit=3, user_id=PROBE_USER_ID
        )
        report["dense_multi_recall"] = {
            "ok": True,
            "count": len(candidates),
            "routes": sorted({route for item in candidates for route in item.get("routes", set())}),
        }
    except Exception as exc:
        report["dense_multi_recall"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true", help="先写入探针片段再查询")
    args = parser.parse_args()
    lift_llm_limits()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
