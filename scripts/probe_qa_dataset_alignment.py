"""检查 qa_dataset 引用的文档范围是否与 corpus_manifest 一致（标签失配归因用）。

用法：python -B scripts/probe_qa_dataset_alignment.py --report qa_alignment_report.txt
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="qa_alignment_report.txt")
    args = parser.parse_args()

    dataset = json.loads((ROOT / "eval/qa_dataset.json").read_text(encoding="utf-8"))
    manifest = json.loads((ROOT / "eval/corpus_manifest.json").read_text(encoding="utf-8"))
    cases = dataset if isinstance(dataset, list) else (dataset.get("cases") or dataset.get("items") or [])
    manifest = manifest if isinstance(manifest, list) else (manifest.get("documents") or [])
    manifest_ids = sorted(int(entry["document_id"]) for entry in manifest)
    case_ids = Counter(case.get("document_id") for case in cases)

    lines = [
        f"dataset cases: {len(cases)}",
        f"manifest docs: {manifest_ids}",
        f"case document_id 分布: {dict(sorted(case_ids.items(), key=lambda kv: str(kv[0])))}",
        "",
    ]
    missing = sorted({cid for cid in case_ids if cid not in manifest_ids})
    lines.append(f"数据集引用但 manifest 未提供的 document_id: {missing}")
    for entry in manifest:
        lines.append(f"  manifest doc {entry['document_id']}: {entry.get('document_name')} <- {entry.get('file_path')}")
    lines.append("")
    lines.append("逐题（document_id | name | 期望证据关键词是否存在语料里）:")
    texts = {}
    for entry in manifest:
        path = ROOT / entry["file_path"]
        if not path.exists():
            path = ROOT / "eval" / Path(entry["file_path"]).name
        texts[int(entry["document_id"])] = path.read_text(encoding="utf-8", errors="ignore")
    for case in cases:
        missed = []
        for keyword in case.get("expected_chunk_keywords") or []:
            if not any(keyword in text for text in texts.values()):
                missed.append(keyword)
        lines.append(
            "{:>5} | {:<38} | 缺失关键词 {} 个 {}".format(
                case.get("document_id"),
                str(case.get("name") or case.get("question"))[:38],
                len(missed),
                missed[:3],
            )
        )
    (ROOT / args.report).write_text("\n".join(lines), encoding="utf-8")
    print(f"written {args.report}; 未提供的 document_id={missing}; 有缺失关键词的题数待看报告")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
