"""Graph RAG ablation regression for candidate-set preservation and ranking.

This is a deterministic fixture test. It does not claim real-law quality and
does not require Neo4j or an embedding API. Use it to ensure graph evidence
only reorders already-retrieved near-tie candidates.

Usage:
    python eval/run_graph_rag_eval.py --pretty
    python eval/run_graph_rag_eval.py --output eval/outputs/graph_rag_ablation_report.json
"""

from __future__ import annotations

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
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.legal import LegalArticle, LegalSource
from app.services.legal_retrieval_service import LegalRetrievalService


EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CASES_PATH = EVAL_DIR / "graph_rag_eval_cases.json"


class NoopCollection:
    def query(self, **_kwargs):
        return {"metadatas": [[]]}


class NoopLLMClient:
    async def embed(self, texts, **_kwargs):
        return [[0.0, 0.0, 0.0, 0.0] for _ in texts]


class StaticGraphEvidence:
    def __init__(self, evidence: dict[int, dict]):
        self.evidence = evidence

    async def relation_evidence(self, *, user_id: int, article_ids: list[int]) -> dict[int, dict]:
        _ = user_id
        return {article_id: self.evidence[article_id] for article_id in article_ids if article_id in self.evidence}


async def evaluate_case(case: dict) -> dict:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        article_ids: list[int] = []
        for index, item in enumerate(case["articles"]):
            source = LegalSource(
                user_id=1,
                title=item["title"],
                source_type="statute",
                citation=item["article_number"],
                jurisdiction="中国大陆",
                version=f"fixture-{index + 1}",
                status="active",
                content=item["content"],
            )
            db.add(source)
            db.flush()
            article = LegalArticle(
                source_id=source.id,
                article_number=item["article_number"],
                content=item["content"],
                sequence=1,
            )
            db.add(article)
            db.flush()
            article_ids.append(article.id)
        db.commit()

        no_graph_service = LegalRetrievalService(
            client=NoopLLMClient(),
            collection=NoopCollection(),
            graph_service=StaticGraphEvidence({}),
        )
        baseline = await no_graph_service.search(db, case["question"], user_id=1, limit=5)

        support = case["graph_support"]
        supported_article_id = article_ids[int(support["article_index"])]
        graph_evidence = {
            supported_article_id: {
                "version_relations": support.get("version_relations", []),
                "related_article_ids": [article_ids[0]],
                "shared_law_area": bool(support.get("shared_law_area")),
                "support_count": int(support["support_count"]),
            }
        }
        graph_service = LegalRetrievalService(
            client=NoopLLMClient(),
            collection=NoopCollection(),
            graph_service=StaticGraphEvidence(graph_evidence),
        )
        with_graph = await graph_service.search(db, case["question"], user_id=1, limit=5)

        expected_id = article_ids[int(case["expected_promoted_article_index"])]
        baseline_ids = [item["id"] for item in baseline]
        graph_ids = [item["id"] for item in with_graph]
        supported_result = next((item for item in with_graph if item["id"] == expected_id), {})
        return {
            "name": case["name"],
            "candidate_set_unchanged": set(baseline_ids) == set(graph_ids),
            "baseline_ranked_article_ids": baseline_ids,
            "graph_ranked_article_ids": graph_ids,
            "expected_promoted_article_id": expected_id,
            "promotion_correct": bool(graph_ids and graph_ids[0] == expected_id),
            "graph_support": supported_result.get("score_breakdown", {}).get("graph_support"),
        }
    finally:
        db.close()
        engine.dispose()


async def run_eval(cases: list[dict]) -> dict:
    results = [await evaluate_case(case) for case in cases]
    total = len(results)
    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "synthetic_relation_evidence_ablation",
        "total_cases": total,
        "candidate_set_stability": round(sum(item["candidate_set_unchanged"] for item in results) / total, 4) if total else None,
        "promotion_accuracy": round(sum(item["promotion_correct"] for item in results) / total, 4) if total else None,
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Graph RAG 候选集保持与排序提升回归评测")
    parser.add_argument("--cases-path", default=str(DEFAULT_CASES_PATH))
    parser.add_argument("--output", default=None)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    cases = json.loads(Path(args.cases_path).read_text(encoding="utf-8"))
    report = asyncio.run(run_eval(cases))
    text = json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"报告已写入 {output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
