import argparse
import asyncio
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app.models  # noqa: F401
from eval.bundle_utils import DEFAULT_DATASET_PATH, load_bundle_meta, resolve_eval_paths
from eval.common import ensure_eval_llm_ready
from app.services.rag.agentic_rag_service import agentic_rag_service
from app.services.llm.prompt_service import prompt_service
from app.services.rag.rag_service import rag_service


def load_dataset(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError("Dataset must be a list")
    return data


def dataset_fingerprint(dataset: list[dict]) -> str:
    """Produce a stable dataset identifier for experiment comparison."""
    canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_dataset(dataset: list[dict]) -> list[str]:
    errors = []
    for index, item in enumerate(dataset, start=1):
        if not isinstance(item, dict):
            errors.append(f"case {index}: must be an object")
            continue
        if not str(item.get("question") or "").strip():
            errors.append(f"case {index}: question is required")
        if not item.get("should_refuse") and not item.get("expected_chunk_keywords"):
            errors.append(f"case {index}: answerable case requires expected_chunk_keywords")
        if item.get("expected_answer_keywords") and not isinstance(item.get("expected_answer_keywords"), list):
            errors.append(f"case {index}: expected_answer_keywords must be a list")
    return errors


def _normalize_expected_keywords(item: dict) -> list[str]:
    keywords = item.get("expected_chunk_keywords")
    if keywords:
        return [keyword for keyword in keywords if keyword]
    references = item.get("expected_citation_keywords")
    if references:
        return [keyword for keyword in references if keyword]
    return []


def keyword_hit(expected_keywords: list[str], hit_chunks: list[dict]) -> bool:
    if not expected_keywords:
        return True
    corpus = "\n".join((chunk.get("content") or "") for chunk in hit_chunks)
    return any(keyword and keyword in corpus for keyword in expected_keywords)


def citation_hit(expected_keywords: list[str], citations: list[dict]) -> bool:
    if not expected_keywords:
        return True
    citation_text = "\n".join((item.get("source_text") or "") for item in citations)
    return any(keyword and keyword in citation_text for keyword in expected_keywords)


def _normalize_answer_text(value: str) -> str:
    """Remove presentation-only differences before matching answer labels."""
    normalized = (value or "").lower()
    return re.sub(r"[\s\u3000,，.。;；:：!！?？()（）\[\]【】{}<>《》\"'“”‘’、]+", "", normalized)


def answer_hit(expected_keywords: list[str], answer: str) -> bool | None:
    if not expected_keywords:
        return None
    normalized_answer = _normalize_answer_text(answer)
    return all(_normalize_answer_text(keyword) in normalized_answer for keyword in expected_keywords)


def _classify_case(item: dict, result: dict, *, hit: bool, citation_ok: bool) -> str:
    should_refuse = bool(item.get("should_refuse"))
    can_answer = bool(result.get("can_answer", False))
    if should_refuse:
        return "correct_refusal" if not can_answer else "missed_refusal"
    if not can_answer:
        return "false_refusal"
    if not hit:
        return "retrieval_miss"
    if not citation_ok:
        return "citation_miss"
    return "pass"


def collect_badcases(cases: list[dict]) -> list[dict]:
    non_badcase_outcomes = {"pass", "correct_refusal"}
    return [case for case in cases if case.get("case_outcome") not in non_badcase_outcomes]


def _percentile(sorted_values: list[float], p: int) -> float:
    """线性插值分位（sorted 升序）；空列表返回 0.0。"""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    idx = (n - 1) * p / 100.0
    lower = int(idx)
    upper = min(lower + 1, n - 1)
    weight = idx - lower
    return round(sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight, 2)


def run_eval(
    dataset: list[dict],
    *,
    user_id: int | None,
    top_k: int | None,
    confidence_threshold: float | None,
    min_recall_candidates: int | None = None,
    recall_multiplier: int | None = None,
    query_variant_limit: int | None = None,
    context_neighbor_window: int | None = None,
    context_max_chunks: int | None = None,
    bundle_meta: dict | None = None,
) -> dict:
    runtime_config = rag_service.get_runtime_config(
        top_k=top_k,
        confidence_threshold=confidence_threshold,
        min_recall_candidates=min_recall_candidates,
        recall_multiplier=recall_multiplier,
        query_variant_limit=query_variant_limit,
        context_neighbor_window=context_neighbor_window,
        context_max_chunks=context_max_chunks,
    )
    prompt_metadata = prompt_service.get_template_metadata("rag_answer")
    totals = {
        "count": len(dataset),
        "answerable_count": 0,
        "refusal_count": 0,
        "hit_count": 0,
        "citation_hit_count": 0,
        "refusal_correct_count": 0,
        "answer_labeled_count": 0,
        "answer_correct_count": 0,
        "latency_ms_total": 0,
        "agentic_retrieval_rounds_total": 0,
    }
    cases = []

    for item in dataset:
        should_refuse = bool(item.get("should_refuse"))
        expected_keywords = _normalize_expected_keywords(item)
        started = time.perf_counter()
        result = agentic_rag_service.answer(
            item["question"],
            document_id=item.get("document_id"),
            user_id=user_id,
            top_k=runtime_config["top_k"],
            confidence_threshold=runtime_config["confidence_threshold"],
            min_recall_candidates=runtime_config["min_recall_candidates"],
            recall_multiplier=runtime_config["recall_multiplier"],
            query_variant_limit=runtime_config["query_variant_limit"],
            context_neighbor_window=runtime_config["context_neighbor_window"],
            context_max_chunks=runtime_config["context_max_chunks"],
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        agentic_trace = result.get("agentic_rag") or {}
        retrieval_rounds = int(agentic_trace.get("retrieval_rounds") or 0)
        hit = keyword_hit(expected_keywords, result.get("hit_chunks") or [])
        citation_ok = citation_hit(expected_keywords, result.get("citations") or [])
        expected_answer_keywords = [str(item).strip() for item in item.get("expected_answer_keywords") or [] if str(item).strip()]
        answer_ok = answer_hit(expected_answer_keywords, result.get("answer", ""))
        refusal_correct = should_refuse and not result.get("can_answer", False)
        case_outcome = _classify_case(item, result, hit=hit, citation_ok=citation_ok)

        if should_refuse:
            totals["refusal_count"] += 1
            if refusal_correct:
                totals["refusal_correct_count"] += 1
        else:
            totals["answerable_count"] += 1
            if hit:
                totals["hit_count"] += 1
            if result.get("can_answer", False) and citation_ok:
                totals["citation_hit_count"] += 1
            if answer_ok is not None:
                totals["answer_labeled_count"] += 1
                if answer_ok:
                    totals["answer_correct_count"] += 1
        totals["latency_ms_total"] += latency_ms
        totals["agentic_retrieval_rounds_total"] += retrieval_rounds

        cases.append(
            {
                "name": item.get("name") or item["question"],
                "document_id": item.get("document_id"),
                "document_name": item.get("document_name"),
                "category": item.get("category"),
                "question": item["question"],
                "reference_answer": item.get("reference_answer", ""),
                "expected_chunk_keywords": expected_keywords,
                "expected_answer_keywords": expected_answer_keywords,
                "should_refuse": should_refuse,
                "can_answer": result.get("can_answer", False),
                "confidence": result.get("confidence", 0.0),
                "hit": hit,
                "citation_hit": citation_ok,
                "answer_hit": answer_ok,
                "latency_ms": latency_ms,
                "agentic_rag": agentic_trace,
                "retrieval_rounds": retrieval_rounds,
                "case_outcome": case_outcome,
                "answer": result.get("answer", ""),
                "citations": result.get("citations", []),
            }
        )

    answerable_count = totals["answerable_count"] or 1
    refusal_count = totals["refusal_count"] or 1
    badcases = collect_badcases(cases)
    fingerprint = dataset_fingerprint(dataset)
    latencies = sorted(c.get("latency_ms") or 0 for c in cases)
    latency_p50 = _percentile(latencies, 50)
    latency_p95 = _percentile(latencies, 95)
    return {
        "evaluation_id": f"rag_eval_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{fingerprint[:8]}",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "top_k": runtime_config["top_k"],
            "confidence_threshold": runtime_config["confidence_threshold"],
            "min_recall_candidates": runtime_config["min_recall_candidates"],
            "recall_multiplier": runtime_config["recall_multiplier"],
            "query_variant_limit": runtime_config["query_variant_limit"],
            "context_neighbor_window": runtime_config["context_neighbor_window"],
            "context_max_chunks": runtime_config["context_max_chunks"],
            "prompt_template": prompt_metadata.get("prompt_template"),
            "prompt_version": prompt_metadata.get("prompt_version"),
            "rag_engine": "agentic_rag",
            "user_id": user_id,
            "bundle_meta": bundle_meta or {},
            "dataset_fingerprint": fingerprint,
        },
        "summary": {
            "total_cases": totals["count"],
            "answerable_cases": totals["answerable_count"],
            "refusal_cases": totals["refusal_count"],
            "hit_at_k": round(totals["hit_count"] / answerable_count, 4) if totals["answerable_count"] else None,
            "citation_accuracy": round(totals["citation_hit_count"] / answerable_count, 4) if totals["answerable_count"] else None,
            "refusal_accuracy": round(totals["refusal_correct_count"] / refusal_count, 4) if totals["refusal_count"] else None,
            "hit_count": totals["hit_count"],
            "citation_hit_count": totals["citation_hit_count"],
            "refusal_correct_count": totals["refusal_correct_count"],
            "answer_labeled_cases": totals["answer_labeled_count"],
            "answer_correct_count": totals["answer_correct_count"],
            "answer_accuracy": round(totals["answer_correct_count"] / totals["answer_labeled_count"], 4) if totals["answer_labeled_count"] else None,
            "average_latency_ms": round(totals["latency_ms_total"] / totals["count"], 2) if totals["count"] else None,
            "latency_p50_ms": latency_p50,
            "latency_p95_ms": latency_p95,
            "average_retrieval_rounds": round(
                totals["agentic_retrieval_rounds_total"] / totals["count"], 2
            ) if totals["count"] else None,
            "badcase_count": len(badcases),
        },
        "cases": cases,
        "badcases": badcases,
    }


async def run_eval_with_llm_judge(**kwargs) -> dict:
    """Run deterministic evaluation, then attach explicitly auxiliary judge scores."""
    from eval.llm_judge import judge_cases, summarize_judgements

    result = run_eval(**kwargs)
    judgements = await judge_cases(result["cases"])
    for case, judgement in zip(result["cases"], judgements):
        case["llm_judge"] = judgement
    result["summary"]["llm_judge"] = summarize_judgements(judgements)
    result["config"]["llm_judge_enabled"] = True
    return result


def write_eval_output(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RAG evaluation on qa_dataset.json")
    parser.add_argument("--bundle-dir", default=None, help="Directory of an eval bundle containing manifest/dataset/matrix")
    parser.add_argument("--dataset", default=None, help="Path to dataset json; overrides bundle dataset when provided")
    parser.add_argument("--user-id", type=int, default=None, help="Optional user id filter")
    parser.add_argument("--top-k", type=int, default=5, help="RAG retrieval top_k")
    parser.add_argument("--confidence-threshold", type=float, default=0.35, help="RAG refusal threshold")
    parser.add_argument("--context-neighbor-window", type=int, default=None, help="Neighbor chunks added around retrieval hits")
    parser.add_argument("--context-max-chunks", type=int, default=None, help="Maximum chunks passed into the answer prompt")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print output json")
    parser.add_argument("--llm-judge", action="store_true", help="Enable optional LLM-as-Judge auxiliary scores")
    parser.add_argument("--output", default=None, help="Optional path for the full evaluation JSON report")
    parser.add_argument("--validate-only", action="store_true", help="Validate dataset annotations without calling the RAG service")
    return parser.parse_args()


def main():
    args = parse_args()
    paths = resolve_eval_paths(bundle_dir=args.bundle_dir, dataset_path=args.dataset)
    dataset = load_dataset(paths["dataset_path"])
    errors = validate_dataset(dataset)
    if args.validate_only:
        payload = {"valid": not errors, "case_count": len(dataset), "errors": errors}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if not errors else 1
    if errors:
        raise ValueError("Invalid evaluation dataset: " + "; ".join(errors[:5]))
    kwargs = {
        "dataset": dataset,
        "user_id": args.user_id,
        "top_k": args.top_k,
        "confidence_threshold": args.confidence_threshold,
        "context_neighbor_window": args.context_neighbor_window,
        "context_max_chunks": args.context_max_chunks,
        "bundle_meta": load_bundle_meta(paths["bundle_dir"]),
    }
    if args.llm_judge:
        ensure_eval_llm_ready()
        results = asyncio.run(run_eval_with_llm_judge(**kwargs))
    else:
        results = run_eval(**kwargs)
    if args.output:
        write_eval_output(Path(args.output), results)

    if args.pretty:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
