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
import math
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


def relevant_ranks(ranked_chunks: list[dict], document_id: int, match: str | list[str]) -> list[int]:
    """返回全部相关片段排名；match 可为字符串或列表（支持多相关项，供 nDCG 用）。"""
    matches = [match] if isinstance(match, str) else list(match)
    ranks = []
    for index, chunk in enumerate(ranked_chunks, start=1):
        metadata = chunk.get("metadata") or {}
        if metadata.get("document_id") == document_id and any(m in chunk.get("content", "") for m in matches):
            ranks.append(index)
    return ranks


def ndcg_at_k(ranked_chunks: list[dict], document_id: int, match: str | list[str], k: int) -> float:
    """nDCG@K：单个相关片段时退化为 1/log2(rank+1)；多相关项时按理想增益归一化。"""
    ranks = [r for r in relevant_ranks(ranked_chunks, document_id, match) if r <= k]
    if not ranks:
        return 0.0
    dcg = sum(1.0 / math.log2(rank + 1) for rank in ranks)
    ideal = sum(1.0 / math.log2(index + 2) for index in range(len(ranks)))
    return dcg / ideal if ideal > 0 else 0.0


def calibrate_threshold(pairs: list[tuple[float, bool]]) -> float | None:
    """在离线命中/未命中标注上扫描置信度阈值，返回最大化 F1 的建议阈值。

    全命中或全未命中（无区分度）时返回 None，提示需要更多样例。
    """
    if not pairs or all(hit for _, hit in pairs) or not any(hit for _, hit in pairs):
        return None
    best_threshold = None
    best_f1 = -1.0
    for threshold in sorted({confidence for confidence, _ in pairs}):
        tp = sum(1 for c, hit in pairs if c >= threshold and hit)
        fp = sum(1 for c, hit in pairs if c >= threshold and not hit)
        fn = sum(1 for c, hit in pairs if c < threshold and hit)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = threshold
    return round(best_threshold, 4) if best_threshold is not None else None


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
    ranked_results: list[list[dict]] = []
    margins: list[float | None] = []
    route_consistent: list[bool] = []
    confidences: list[float] = []
    for item in qa:
        ranked = service.search(item["question"], user_id=1, top_k=args.top_k)
        rank = hit_at_k(ranked, item["document_id"], item["match"])
        hits.append(rank)
        ranked_results.append(ranked)
        top_score = ranked[0].get("retrieval_score") if ranked else None
        second_score = ranked[1].get("retrieval_score") if len(ranked) >= 2 else None
        margin = None
        if top_score is not None and second_score is not None and top_score > 0:
            margin = max(0.0, min(1.0, (top_score - second_score) / top_score))
        margins.append(margin)
        routes = (ranked[0].get("retrieval_routes") or []) if ranked else []
        route_consistent.append(len(routes) >= 2)
        confidences.append(service._estimate_confidence(item["question"], ranked))
        status = f"Hit@{rank}" if rank is not None else "MISS"
        print(f"  [{status}] {item['question']}")

    hit1 = sum(1 for r in hits if r is not None and r <= 1) / len(qa)
    hitk = sum(1 for r in hits if r is not None and r <= args.top_k) / len(qa)
    mrr = sum(1.0 / r for r in hits if r is not None) / len(qa)
    ndcg = sum(
        ndcg_at_k(ranked, item["document_id"], item["match"], args.top_k)
        for ranked, item in zip(ranked_results, qa)
    ) / len(qa)
    scored_margins = [m for m in margins if m is not None]
    calibration_hint = calibrate_threshold(list(zip(confidences, [r is not None for r in hits])))

    report = {
        "eval": "document_rag_retrieval",
        "total_questions": len(qa),
        "top_k": args.top_k,
        "hit_at_1": round(hit1, 4),
        f"hit_at_{args.top_k}": round(hitk, 4),
        "mrr": round(mrr, 4),
        f"ndcg_at_{args.top_k}": round(ndcg, 4),
        "top_score_margin_avg": round(sum(scored_margins) / len(scored_margins), 4) if scored_margins else None,
        "route_consistency_rate": round(sum(route_consistent) / len(route_consistent), 4),
        "confidence_calibration_hint": calibration_hint,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
