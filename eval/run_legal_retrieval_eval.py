"""法律检索评测：衡量 _rank_sources_by_relevance 的 Hit@K / MRR / 拒答准确率。

纯规则检索逻辑评测，不调用任何 LLM，零外部成本，可随时重跑。

用法：
    python eval/run_legal_retrieval_eval.py --pretty
    python eval/run_legal_retrieval_eval.py --top-k 3 --output eval/outputs/legal_retrieval_report.json
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models.legal import LegalSource
from app.services.legal.legal_service import _rank_sources_by_relevance

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS_PATH = EVAL_DIR / "legal_sources_corpus.json"
DEFAULT_QA_PATH = EVAL_DIR / "legal_retrieval_qa.json"


def load_json(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_sources(corpus: list[dict]) -> list[LegalSource]:
    sources = []
    for item in corpus:
        sources.append(
            LegalSource(
                id=item["id"],
                user_id=1,
                title=item["title"],
                source_type=item.get("source_type", "statute"),
                citation=item.get("citation", ""),
                jurisdiction="中国大陆",
                version="v1",
                status=item.get("status", "active"),
                content=item.get("content", ""),
            )
        )
    return sources


def reciprocal_rank(ranked_ids: list[int], expected_ids: set[int]) -> float:
    if not expected_ids:
        return 0.0
    for idx, sid in enumerate(ranked_ids, start=1):
        if sid in expected_ids:
            return 1.0 / idx
    return 0.0


def hit_at_k(ranked_ids: list[int], expected_ids: set[int], k: int) -> bool:
    if not expected_ids:
        return len(ranked_ids) == 0
    return any(sid in expected_ids for sid in ranked_ids[:k])


def run_eval(corpus: list[dict], qa_dataset: list[dict], top_k: int = 5) -> dict:
    sources = build_sources(corpus)

    per_case = []
    hit_count = 0
    mrr_total = 0.0
    correct_refusal_count = 0
    refusal_case_count = 0
    avoided_source_violations = 0

    for item in qa_dataset:
        expected_ids = set(item.get("expected_source_ids", []))
        avoid_ids = set(item.get("avoid_source_ids", []))
        is_refusal_case = len(expected_ids) == 0

        ranked = _rank_sources_by_relevance(item["question"], sources)
        ranked_ids = [s.id for s in ranked]

        hit = hit_at_k(ranked_ids, expected_ids, top_k)
        mrr = reciprocal_rank(ranked_ids, expected_ids) if not is_refusal_case else (1.0 if not ranked_ids else 0.0)

        if is_refusal_case:
            refusal_case_count += 1
            if not ranked_ids:
                correct_refusal_count += 1
        else:
            hit_count += int(hit)
            mrr_total += mrr

        # avoid_source 仅在"排名超过正确答案"时才算违规——干扰项出现在候选
        # 列表低位、但未抢占正确排名，属于正常的相关性召回，不算 badcase。
        avoid_hit = False
        if avoid_ids and expected_ids:
            first_expected_rank = next((i for i, sid in enumerate(ranked_ids) if sid in expected_ids), None)
            first_avoid_rank = next((i for i, sid in enumerate(ranked_ids) if sid in avoid_ids), None)
            if first_avoid_rank is not None and (first_expected_rank is None or first_avoid_rank < first_expected_rank):
                avoid_hit = True
        if avoid_hit:
            avoided_source_violations += 1

        per_case.append({
            "name": item["name"],
            "question": item["question"],
            "expected_source_ids": sorted(expected_ids),
            "ranked_source_ids_top_k": ranked_ids[:top_k],
            "hit": hit if not is_refusal_case else (not ranked_ids),
            "reciprocal_rank": round(mrr, 4),
            "is_refusal_case": is_refusal_case,
            "avoid_source_violation": avoid_hit,
            "note": item.get("note", ""),
        })

    answerable_count = len(qa_dataset) - refusal_case_count
    hit_at_k_rate = round(hit_count / answerable_count, 4) if answerable_count else None
    mrr_rate = round(mrr_total / answerable_count, 4) if answerable_count else None
    refusal_accuracy = round(correct_refusal_count / refusal_case_count, 4) if refusal_case_count else None

    badcases = [c for c in per_case if not c["hit"] or c["avoid_source_violation"]]

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "top_k": top_k,
        "total_cases": len(qa_dataset),
        "answerable_cases": answerable_count,
        "refusal_cases": refusal_case_count,
        f"hit_at_{top_k}": hit_at_k_rate,
        "mrr": mrr_rate,
        "refusal_accuracy": refusal_accuracy,
        "avoid_source_violations": avoided_source_violations,
        "badcases": badcases,
        "cases": per_case,
    }


def main():
    parser = argparse.ArgumentParser(description="法律检索评测：Hit@K / MRR / 拒答准确率")
    parser.add_argument("--corpus-path", type=str, default=str(DEFAULT_CORPUS_PATH))
    parser.add_argument("--qa-path", type=str, default=str(DEFAULT_QA_PATH))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    corpus = load_json(Path(args.corpus_path))
    qa_dataset = load_json(Path(args.qa_path))
    report = run_eval(corpus, qa_dataset, top_k=args.top_k)

    indent = 2 if args.pretty else None
    output_text = json.dumps(report, ensure_ascii=False, indent=indent)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text, encoding="utf-8")
        print(f"报告已写入 {output_path}")
    else:
        print(output_text)

    hit_key = f"hit_at_{report['top_k']}"
    summary = (
        f"\n=== 摘要 === "
        f"Hit@{report['top_k']}={report.get(hit_key)} "
        f"MRR={report['mrr']} "
        f"拒答准确率={report['refusal_accuracy']} "
        f"badcase数={len(report['badcases'])}"
    )
    print(summary, file=sys.stderr)


if __name__ == "__main__":
    main()
