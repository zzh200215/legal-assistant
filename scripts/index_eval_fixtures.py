"""把 eval 夹具（3 份样例文档）用真实嵌入索引到当前 CHROMA_PERSIST_DIR。

为什么单独写驱动而不用 `eval/index_eval_corpus.py`：
`rag_service.index_document` 是同步函数，内部走 `run_async`（线程池 + 每次新建事件
循环），而 `LLMClient._get_client` 按 (base_url, key, timeout) 缓存 httpx 客户端、
与事件循环无关 —— 第一份文档索引后缓存的客户端绑定在已关闭的循环上，第二份文档就会
抛 `RuntimeError: Event loop is closed`。这里在每份文档前后清空客户端缓存绕开它。

用法（真实嵌入，进程内抬限流）：
  $env:CHROMA_PERSIST_DIR="<根>/chroma_eval_store"
  python -B scripts/index_eval_fixtures.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.core.llm_client import llm_client
from app.services.documents.document_service import _extract_segments, _split_text
from app.services.rag.rag_service import rag_service

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def lift_llm_limits() -> None:
    settings = get_settings()
    settings.LLM_DAILY_REQUEST_LIMIT = 1_000_000
    settings.LLM_DAILY_TOKEN_LIMIT = 1_000_000_000
    settings.LLM_RATE_LIMIT_MAX_REQUESTS = 1_000_000
    settings.LLM_RATE_LIMIT_CONFIG_JSON = '{"embedding": {"window_seconds": 60, "max_requests": 1000000}}'
    settings.LLM_BUDGET_LIMITS_JSON = '{"embedding": {"daily_requests": 1000000, "daily_tokens": 1000000000}}'


def build_chunks(document_id: int, file_path: Path, file_type: str) -> list[dict]:
    segments = _extract_segments(str(file_path), file_type)
    chunks = _split_text(segments, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    return [
        {
            "id": None,
            "chunk_index": chunk["chunk_index"],
            "content": chunk["content"],
            "page_number": chunk.get("page_number"),
            "section_title": chunk.get("section_title"),
            "section_path": chunk.get("section_path"),
            "segment_type": chunk.get("segment_type"),
            "table_like": chunk.get("table_like"),
            "embedding_id": f"doc{document_id}_chunk{chunk['chunk_index']}",
        }
        for chunk in chunks
    ]


def main() -> int:
    lift_llm_limits()
    settings = get_settings()
    manifest = json.loads((ROOT / "eval" / "corpus_manifest.json").read_text(encoding="utf-8"))
    report: dict = {
        "chroma_persist_dir": settings.CHROMA_PERSIST_DIR,
        "embedding_model": settings.EMBEDDING_MODEL,
        "documents": [],
    }
    for item in manifest:
        chunks = build_chunks(item["document_id"], ROOT / item["file_path"], item["file_type"])
        llm_client._clients.clear()
        started = time.perf_counter()
        rag_service.index_document(item["document_id"], chunks, user_id=item["user_id"])
        llm_client._clients.clear()
        report["documents"].append(
            {
                "document_id": item["document_id"],
                "document_name": item["document_name"],
                "chunks": len(chunks),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )
    report["collection_count"] = rag_service.collection.collection.count()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
