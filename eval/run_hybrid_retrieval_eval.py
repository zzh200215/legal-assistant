"""Evaluates LegalRetrievalService.search() — the production hybrid (lexical + dense) search path.

Unlike eval/run_legal_retrieval_eval.py (which tests the legacy _rank_sources_by_relevance),
this script evaluates the article-level hybrid service wired into /article-search.

Modes:
  python eval/run_hybrid_retrieval_eval.py --lexical-only  # no embeddings, fast
  python eval/run_hybrid_retrieval_eval.py --index         # full hybrid (requires LLM_API_KEY)

Metrics match run_legal_retrieval_eval.py for cross-comparison:
  Hit@K, MRR, refusal_accuracy, avoid_source_violations
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.models  # noqa: F401
import chromadb
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.llm_client import LLMClient
from app.models.legal import LegalArticle, LegalSource
from app.services.legal_retrieval_service import LegalRetrievalService
from app.services.vector_store import ChromaVectorStoreCollection
from eval.common import ensure_eval_llm_ready

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS_PATH = EVAL_DIR / "legal_sources_corpus.json"
DEFAULT_QA_PATH = EVAL_DIR / "legal_retrieval_qa.json"


def load_json(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _build_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def build_db(corpus: list[dict]) -> tuple[Session, dict[int, int]]:
    """Populate in-memory SQLite from corpus items.

    Each corpus item becomes one LegalSource + one LegalArticle.
    Returns (session, corpus_id_to_source_id mapping).
    """
    engine = _build_engine()
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()

    corpus_id_to_source_id: dict[int, int] = {}
    for item in corpus:
        src = LegalSource(
            user_id=1,
            title=item["title"],
            source_type=item.get("source_type", "statute"),
            citation=item.get("citation", ""),
            jurisdiction="中国大陆",
            version="v1",
            status=item.get("status", "active"),
            content=item.get("content", ""),
        )
        db.add(src)
        db.flush()
        corpus_id_to_source_id[item["id"]] = src.id

        article = LegalArticle(
            source_id=src.id,
            article_number=item.get("citation", item["title"]),
            content=item.get("content", ""),
            sequence=1,
        )
        db.add(article)

    db.commit()
    return db, corpus_id_to_source_id


def build_service(*, index_embeddings: bool, db: Session) -> LegalRetrievalService:
    """Build LegalRetrievalService with in-memory Chroma.

    When index_embeddings=True, index all articles using the real LLM client.
    When False, the service still runs but dense recall will silently return nothing.
    """
    chroma_client = chromadb.EphemeralClient()
    raw_collection = chroma_client.get_or_create_collection("hybrid_eval_legal_articles")
    collection = ChromaVectorStoreCollection(raw_collection)
    client = LLMClient()
    service = LegalRetrievalService(client=client, collection=collection)

    if index_embeddings:
        sources = db.query(LegalSource).filter(LegalSource.user_id == 1).all()
        for src in sources:
            asyncio.run(service.index_source(db, src.id, user_id=1))

    return service


# ── Metrics ──────────────────────────────────────────────────────────────────

def reciprocal_rank(source_ids: list[int], expected: set[int]) -> float:
    if not expected:
        return 0.0
    for rank, sid in enumerate(source_ids, start=1):
        if sid in expected:
            return 1.0 / rank
    return 0.0


def hit_at_k(source_ids: list[int], expected: set[int], k: int) -> bool:
    if not expected:
        return len(source_ids) == 0
    return any(sid in expected for sid in source_ids[:k])


def avoid_violation(source_ids: list[int], expected: set[int], avoid: set[int]) -> bool:
    """True if an avoid source ranks above the first expected source."""
    if not avoid or not expected:
        return False
    first_expected = next((i for i, sid in enumerate(source_ids) if sid in expected), None)
    first_avoid = next((i for i, sid in enumerate(source_ids) if sid in avoid), None)
    return first_avoid is not None and (first_expected is None or first_avoid < first_expected)


# ── Eval loop ────────────────────────────────────────────────────────────────

async def run_eval_async(
    qa_dataset: list[dict],
    db: Session,
    service: LegalRetrievalService,
    corpus_id_to_source_id: dict[int, int],
    top_k: int,
) -> dict:
    per_case = []
    hit_count = 0
    mrr_total = 0.0
    correct_refusal_count = 0
    refusal_case_count = 0
    avoid_violations = 0

    for item in qa_dataset:
        expected_corpus_ids = set(item.get("expected_source_ids", []))
        avoid_corpus_ids = set(item.get("avoid_source_ids", []))
        is_refusal = len(expected_corpus_ids) == 0

        # Map corpus ids → DB source ids
        expected_db_ids = {corpus_id_to_source_id[cid] for cid in expected_corpus_ids
                           if cid in corpus_id_to_source_id}
        avoid_db_ids = {corpus_id_to_source_id[cid] for cid in avoid_corpus_ids
                        if cid in corpus_id_to_source_id}

        articles = await service.search(db, item["question"], user_id=1, limit=top_k)
        ranked_source_ids = [a["source_id"] for a in articles]

        hit = hit_at_k(ranked_source_ids, expected_db_ids, top_k)
        mrr = reciprocal_rank(ranked_source_ids, expected_db_ids) if not is_refusal else (1.0 if not ranked_source_ids else 0.0)
        avoided = avoid_violation(ranked_source_ids, expected_db_ids, avoid_db_ids)

        if is_refusal:
            refusal_case_count += 1
            if not ranked_source_ids:
                correct_refusal_count += 1
        else:
            hit_count += int(hit)
            mrr_total += mrr
        if avoided:
            avoid_violations += 1

        per_case.append({
            "name": item["name"],
            "question": item["question"],
            "expected_source_ids": sorted(expected_corpus_ids),
            "ranked_source_ids_top_k": [
                next((cid for cid, dbid in corpus_id_to_source_id.items() if dbid == sid), sid)
                for sid in ranked_source_ids[:top_k]
            ],
            "hit": hit if not is_refusal else (not ranked_source_ids),
            "reciprocal_rank": round(mrr, 4),
            "is_refusal_case": is_refusal,
            "avoid_source_violation": avoided,
            "note": item.get("note", ""),
        })

    answerable = len(qa_dataset) - refusal_case_count
    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "top_k": top_k,
        "total_cases": len(qa_dataset),
        "answerable_cases": answerable,
        "refusal_cases": refusal_case_count,
        f"hit_at_{top_k}": round(hit_count / answerable, 4) if answerable else None,
        "mrr": round(mrr_total / answerable, 4) if answerable else None,
        "refusal_accuracy": round(correct_refusal_count / refusal_case_count, 4) if refusal_case_count else None,
        "avoid_source_violations": avoid_violations,
        "badcases": [c for c in per_case if not c["hit"] or c["avoid_source_violation"]],
        "cases": per_case,
    }


def main():
    parser = argparse.ArgumentParser(description="法律混合检索评测：Hit@K / MRR / 拒答准确率 (LegalRetrievalService)")
    parser.add_argument("--corpus-path", type=str, default=str(DEFAULT_CORPUS_PATH))
    parser.add_argument("--qa-path", type=str, default=str(DEFAULT_QA_PATH))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--lexical-only", action="store_true",
                        help="Skip embedding indexing; dense recall returns nothing (lexical path only)")
    parser.add_argument("--index", action="store_true",
                        help="Index article embeddings before eval (requires LLM_API_KEY)")
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if args.index and not args.lexical_only:
        ensure_eval_llm_ready()

    corpus = load_json(Path(args.corpus_path))
    qa_dataset = load_json(Path(args.qa_path))

    db, corpus_id_to_source_id = build_db(corpus)
    index = args.index and not args.lexical_only
    service = build_service(index_embeddings=index, db=db)

    report = asyncio.run(run_eval_async(qa_dataset, db, service, corpus_id_to_source_id, args.top_k))
    db.close()

    report["mode"] = "lexical_only" if args.lexical_only else ("hybrid_with_index" if index else "hybrid_no_index")
    indent = 2 if args.pretty else None
    output_text = json.dumps(report, ensure_ascii=False, indent=indent)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_text, encoding="utf-8")
        print(f"报告已写入 {out_path}")
    else:
        print(output_text)

    hit_key = f"hit_at_{report['top_k']}"
    print(
        f"\n=== 摘要 [{report['mode']}] === "
        f"Hit@{report['top_k']}={report.get(hit_key)} "
        f"MRR={report['mrr']} "
        f"拒答准确率={report['refusal_accuracy']} "
        f"badcase数={len(report['badcases'])}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
