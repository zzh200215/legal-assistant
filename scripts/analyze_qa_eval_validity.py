"""按「标签是否可用」重算 QA 评测口径：区分产品失败与评测集失效。

判据：题目的 expected_chunk_keywords 是否都能在它自己指向的那篇 fixture 正文里找到。
- 全部找到 / 无期望 → 有效题，计入指标
- 部分缺失（≥1 个能找到）→ 措辞漂移
- 一个都找不到 → 内容漂移（题面描述的东西不在文档里）

用法：python -B scripts/analyze_qa_eval_validity.py --report qa_validity_report.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_fixture_texts() -> dict[int, str]:
    manifest = json.loads((ROOT / "eval/corpus_manifest.json").read_text(encoding="utf-8"))
    manifest = manifest if isinstance(manifest, list) else (manifest.get("documents") or [])
    texts: dict[int, str] = {}
    for entry in manifest:
        path = ROOT / entry["file_path"]
        if not path.exists():
            path = ROOT / "eval" / Path(entry["file_path"]).name
        texts[int(entry["document_id"])] = path.read_text(encoding="utf-8", errors="ignore")
    return texts


def classify(case: dict, texts: dict[int, str]) -> str:
    keywords = case.get("expected_chunk_keywords") or []
    if not keywords:
        return "有效"
    doc_text = texts.get(int(case.get("document_id") or 0), "")
    present = [keyword for keyword in keywords if keyword in doc_text]
    if len(present) == len(keywords):
        return "有效"
    if present:
        return "措辞漂移"
    return "内容漂移"


def metrics(cases: list[dict]) -> dict:
    answerable = [case for case in cases if not case.get("should_refuse")]
    if not answerable:
        return {}
    hits = sum(1 for case in answerable if case.get("hit"))
    citation_hits = sum(1 for case in answerable if case.get("citation_hit"))
    reciprocal = sum(1 for case in answerable if case.get("first_relevant_rank")) / len(answerable)
    ranked = [case for case in answerable if case.get("first_relevant_rank")]
    mrr = sum(1.0 / case["first_relevant_rank"] for case in ranked) / len(answerable)
    coverage = sum(float(case.get("retrieval_evidence_coverage") or 0.0) for case in answerable) / len(answerable)
    return {
        "answerable": len(answerable),
        "hit_at_k": round(hits / len(answerable), 4),
        "mrr": round(mrr, 4),
        "coverage": round(coverage, 4),
        "citation_accuracy": round(citation_hits / len(answerable), 4),
        "with_relevant_rank": round(reciprocal, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="qa_validity_report.txt")
    parser.add_argument("--result", default="eval/outputs/qa_eval_real_generation_judge.json")
    args = parser.parse_args()

    result = json.loads((ROOT / args.result).read_text(encoding="utf-8"))
    texts = load_fixture_texts()
    cases = result["cases"]
    lines: list[str] = [f"result: {args.result}", f"case keys: {sorted(cases[0].keys())}", ""]

    for case in cases:
        case["label_state"] = classify(case, texts)
        lines.append(
            "{} | {:<5} | {:<38} | outcome={:<16} hit={} cov={} rank={} citations={}".format(
                case.get("label_state"),
                case.get("document_id"),
                str(case.get("name"))[:38],
                str(case.get("case_outcome")),
                case.get("hit"),
                case.get("retrieval_evidence_coverage"),
                case.get("first_relevant_rank"),
                len(case.get("citations") or []),
            )
        )

    lines.append("")
    valid = [case for case in cases if case["label_state"] == "有效"]
    lines.append(f"全量口径: {json.dumps(metrics(cases), ensure_ascii=False)}")
    lines.append(f"有效题口径({len(valid)}/{len(cases)}): {json.dumps(metrics(valid), ensure_ascii=False)}")
    lines.append(f"失败题口径: {json.dumps(metrics([c for c in cases if c['label_state'] != '有效']), ensure_ascii=False)}")

    lines.append("")
    lines.append("非 pass 的题逐条归因：")
    for case in cases:
        if case.get("case_outcome") in ("pass", "correct_refusal"):
            continue
        keywords = case.get("expected_chunk_keywords") or []
        doc_text = texts.get(int(case.get("document_id") or 0), "")
        missing = [keyword for keyword in keywords if keyword not in doc_text]
        lines.append(
            "  [{}/{}] {} | 缺失关键词={} | answer={}".format(
                case.get("label_state"),
                case.get("case_outcome"),
                case.get("question"),
                missing,
                (case.get("answer") or "")[:60],
            )
        )

    judge_keys = [key for key in cases[0] if "judge" in key.lower()]
    lines.append("")
    lines.append(f"逐题 judge 字段：{judge_keys}")
    for key in judge_keys:
        scored = [case for case in valid if isinstance(case.get(key), dict)]
        if not scored:
            continue
        dims = sorted(scored[0][key].keys())
        means = {
            dim: round(sum(float(case[key].get(dim) or 0) for case in scored) / len(scored), 4)
            for dim in dims
            if all(isinstance(case[key].get(dim), (int, float)) for case in scored)
        }
        lines.append(f"  有效题 {key} 均值({len(scored)} 题): {json.dumps(means, ensure_ascii=False)}")

    (ROOT / args.report).write_text("\n".join(lines), encoding="utf-8")
    print(f"written {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
