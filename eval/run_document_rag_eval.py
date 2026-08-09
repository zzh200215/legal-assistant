"""文档 RAG 检索评测：Hit@K / MRR。

用确定性离线嵌入（字符双字袋 → 归一化向量）验证混合检索管线（稠密 + BM25/RRF + 重排），
零外部 LLM/嵌入成本，可随时重跑。衡量的是"检索能否召回正确片段"，不评估嵌入模型质量。

用法：
    python -B eval/run_document_rag_eval.py
    python -B eval/run_document_rag_eval.py --top-k 3
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import chromadb

from app.services.document_parsing import _split_text
from app.services.rag_service import RAGService
from app.services.vector_store import ChromaVectorStoreCollection

EVAL_DIR = Path(__file__).resolve().parent
EMBED_DIM = 256


def load_json(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


async def _embed(texts: list[str], user_id=None, action="embedding") -> list[list[float]]:
    """确定性字符双字袋嵌入：相似文本 → 相近向量（离线可跑）。"""
    result = []
    for text in texts:
        compact = re.sub(r"\s+", "", text or "")
        grams = [compact[i:i + 2] for i in range(max(0, len(compact) - 1))]
        vector = [0.0] * EMBED_DIM
        for gram in grams:
            digest = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
            vector[digest % EMBED_DIM] += 1.0
        norm = (sum(x * x for x in vector) ** 0.5) or 1.0
        result.append([round(x / norm, 6) for x in vector])
    return result


def hit_at_k(ranked_chunks: list[dict], document_id: int, match: str) -> int | None:
    """返回相关片段所在排名（1-based），未命中返回 None。"""
    for index, chunk in enumerate(ranked_chunks, start=1):
        metadata = chunk.get("metadata") or {}
        if metadata.get("document_id") == document_id and match in chunk.get("content", ""):
            return index
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Document RAG retrieval eval (offline, deterministic)")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    corpus = load_json(EVAL_DIR / "document_rag_corpus.json")
    qa = load_json(EVAL_DIR / "document_rag_qa.json")

    # 独立临时 collection，避免污染仓库 chroma_db
    chroma_client = chromadb.EphemeralClient()
    collection = ChromaVectorStoreCollection(chroma_client.get_or_create_collection("eval_document_rag"))

    service = RAGService()
    service.collection = collection
    service._bm25_stale = True  # 新 collection，BM25 索引需重建
    service._reranker = None

    # 注入确定性嵌入（离线）
    import app.services.rag_service as rag_module
    rag_module.llm_client.embed = _embed

    # 索引语料（每个文档切块）
    for doc in corpus:
        chunks = _split_text(doc["content"], chunk_size=120, chunk_overlap=20)
        for chunk in chunks:
            chunk["id"] = None
            chunk["embedding_id"] = f"doc{doc['id']}_chunk{chunk['chunk_index']}"
        service.index_document(doc["id"], chunks, user_id=1)

    # 检索评测
    hits = []
    for item in qa:
        ranked = service.search(item["question"], user_id=1, top_k=args.top_k)
        rank = hit_at_k(ranked, item["document_id"], item["match"])
        hits.append(rank)
        status = f"Hit@{rank}" if rank is not None else "MISS"
        print(f"  [{status}] {item['question']}")

    hit1 = sum(1 for r in hits if r is not None and r <= 1) / len(qa)
    hitk = sum(1 for r in hits if r is not None and r <= args.top_k) / len(qa)
    mrr = sum(1.0 / r for r in hits if r is not None) / len(qa)

    report = {
        "eval": "document_rag_retrieval",
        "total_questions": len(qa),
        "top_k": args.top_k,
        "hit_at_1": round(hit1, 4),
        f"hit_at_{args.top_k}": round(hitk, 4),
        "mrr": round(mrr, 4),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
