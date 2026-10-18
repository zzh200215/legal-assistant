"""探针：真实嵌入下「同进程先索引后查询」是否可用（复现应用内真实用户流）。

回答两个问题：
1. 三个评测夹具能否用真实向量供应商完成嵌入与索引；
2. 索引之后再查询（不清理客户端缓存）是否仍可用，还是会撞上绑定在
   索引工作线程事件循环上的 httpx 客户端（`Event loop is closed`）。

运行：python -B scripts/probe_real_embedding_index_query.py
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

DOC_IDS = (9001, 9002, 9003)
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


def build_chunks(document_id: int, file_path: str, file_type: str) -> list[dict]:
    segments = _extract_segments(file_path, file_type)
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


def collection_count() -> int:
    try:
        return rag_service.collection.count()
    except Exception as exc:  # pragma: no cover - probe only
        return f"unavailable:{type(exc).__name__}"


def main() -> int:
    settings = get_settings()
    report: dict = {
        "embedding_provider": settings.EMBEDDING_PROVIDER or "(inherit main)",
        "embedding_model": settings.EMBEDDING_MODEL,
        "collection_count_before": collection_count(),
    }

    for document_id in DOC_IDS:
        rag_service.collection.delete(where={"document_id": document_id})
    report["collection_count_after_purge"] = collection_count()

    manifest = json.loads((ROOT / "eval" / "corpus_manifest.json").read_text(encoding="utf-8"))
    first = manifest[0]
    chunks = build_chunks(first["document_id"], str(ROOT / first["file_path"]), first["file_type"])

    started = time.perf_counter()
    rag_service.index_document(first["document_id"], chunks, user_id=first["user_id"])
    report["index_document"] = {
        "document_id": first["document_id"],
        "chunks": len(chunks),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "cached_clients": len(llm_client._clients),
    }

    started = time.perf_counter()
    try:
        vectors = llm_client.embed(["试用期最长多久"], user_id=first["user_id"], action="probe_query_embed")
        report["query_embed_without_cache_clear"] = {
            "ok": True,
            "dim": len(vectors[0]),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:
        report["query_embed_without_cache_clear"] = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    report["collection_count_after_index"] = collection_count()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
