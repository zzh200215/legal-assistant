"""核对 QA 评测的 badcase 到底是「真失败」还是「标注/语料对不上」。

判据：expected_evidence 的 keywords（match=any 时任一命中即算匹配）是否能在语料里
任一文档的**单一 chunk 内**找到。找不到 → 标注或入库有问题，指标不可直接引用；
找得到但没进 top-5 → 真失败。

用法：python -B scripts/probe_qa_eval_badcases.py --report qa_badcase_report.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHUNK_SIZE = 120
CHUNK_OVERLAP = 20


def chunk_text(text: str) -> list[str]:
    chunks: list[str] = []
    start = 0
    step = CHUNK_SIZE - CHUNK_OVERLAP
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE])
        start += step
    return chunks


def load_corpus_texts() -> dict[int, str]:
    manifest = json.loads((ROOT / "eval/corpus_manifest.json").read_text(encoding="utf-8"))
    texts: dict[int, str] = {}
    for entry in manifest:
        path = ROOT / entry["file_path"]
        if not path.exists():
            path = ROOT / "eval" / Path(entry["file_path"]).name
        texts[int(entry["document_id"])] = path.read_text(encoding="utf-8", errors="ignore")
    return texts


def evidence_found_in_corpus(evidence: list[dict], texts: dict[int, str]) -> bool:
    chunks_by_doc = {doc_id: chunk_text(text) for doc_id, text in texts.items()}
    for item in evidence:
        keywords = item.get("keywords") or []
        match_all = item.get("match") == "all"
        found = False
        for chunks in chunks_by_doc.values():
            for chunk in chunks:
                if match_all and all(keyword in chunk for keyword in keywords):
                    found = True
                    break
                if not match_all and any(keyword in chunk for keyword in keywords):
                    found = True
                    break
            if found:
                break
        if not found:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="qa_badcase_report.txt")
    parser.add_argument("--result", default="eval/outputs/qa_eval_real_generation_judge.json")
    args = parser.parse_args()

    result = json.loads((ROOT / args.result).read_text(encoding="utf-8"))
    texts = load_corpus_texts()
    lines: list[str] = [
        f"result: {args.result}",
        f"summary: {json.dumps(result['summary'], ensure_ascii=False)}",
        f"corpus docs: {sorted(texts)}",
        "",
    ]
    genuine = 0
    label_broken = 0
    for case in result["cases"]:
        if case.get("case_outcome") in ("pass", "correct_refusal"):
            continue
        evidence = case.get("expected_evidence") or []
        in_corpus = evidence_found_in_corpus(evidence, texts) if evidence else None
        if in_corpus:
            genuine += 1
        elif in_corpus is False:
            label_broken += 1
        lines.append(f"[{case.get('case_outcome')}] {case.get('question')}")
        lines.append(
            "  should_refuse={} can_answer={} confidence={} coverage={} first_rank={} ranks={} citations={}".format(
                case.get("should_refuse"),
                case.get("can_answer"),
                case.get("confidence"),
                case.get("retrieval_evidence_coverage"),
                case.get("first_relevant_rank"),
                case.get("relevant_ranks"),
                len(case.get("citations") or []),
            )
        )
        for item in evidence:
            lines.append(f"  evidence[{item.get('id')}|{item.get('match')}]: {item.get('keywords')}")
            for keyword in item.get("keywords") or []:
                raw_docs = [doc_id for doc_id, text in texts.items() if keyword in text]
                lines.append(f"    关键字 {keyword!r} 在整篇文档中出现于：{raw_docs or '（任何文档都没有）'}")
        lines.append(f"  keywords 可在语料单 chunk 内命中：{in_corpus}")
        lines.append(f"  answer: {(case.get('answer') or '')[:120]}")
        lines.append("")

    lines.insert(3, f"badcase 真失败={genuine} 标注/入库对不上={label_broken}")
    (ROOT / args.report).write_text("\n".join(lines), encoding="utf-8")
    print(f"written {args.report}: genuine={genuine} label_broken={label_broken}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
