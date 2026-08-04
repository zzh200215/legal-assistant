import argparse
import json
from pathlib import Path


DEFAULT_MATRIX = [
    {
        "name": "baseline",
        "chunk_size": 800,
        "chunk_overlap": 100,
        "top_k": 5,
        "confidence_threshold": 0.35,
        "context_neighbor_window": 1,
        "context_max_chunks": 8,
    }
]


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def create_bundle(bundle_dir: Path, bundle_name: str) -> dict:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = bundle_dir / "docs"
    docs_dir.mkdir(exist_ok=True)

    bundle_meta = {
        "bundle_name": bundle_name,
        "bundle_type": "business_eval",
        "description": "真实业务文档评测集模板，请替换为你的合同、方案、报告等文档与题集。",
        "recommended_question_count": "25-30",
        "recommended_refusal_count": "3-5",
    }
    manifest = [
        {
            "document_id": 9101,
            "document_name": "请替换为真实文档名称",
            "user_id": 9000,
            "file_path": str((docs_dir / "replace_with_real_document.md").as_posix()),
            "file_type": "md",
        }
    ]
    dataset = [
        {
            "name": "replace_me_payment_amount",
            "document_id": 9101,
            "document_name": "请替换为真实文档名称",
            "category": "payment",
            "question": "请替换为真实业务问题，例如：首付款金额和支付条件是什么？",
            "reference_answer": "请填写标准答案。",
            "expected_chunk_keywords": ["请填写一个或多个关键证据词"],
            "should_refuse": False,
        },
        {
            "name": "replace_me_refusal_case",
            "document_id": 9101,
            "document_name": "请替换为真实文档名称",
            "category": "refusal",
            "question": "请替换为文档中没有答案的问题。",
            "reference_answer": "",
            "expected_chunk_keywords": [],
            "should_refuse": True,
        },
    ]

    _write_json(bundle_dir / "bundle_meta.json", bundle_meta)
    _write_json(bundle_dir / "corpus_manifest.json", manifest)
    _write_json(bundle_dir / "qa_dataset.json", dataset)
    _write_json(bundle_dir / "experiment_matrix.json", DEFAULT_MATRIX)
    (docs_dir / "replace_with_real_document.md").write_text(
        "# 请替换为真实业务文档\n\n将这里替换为脱敏后的合同、方案、尽调报告或其他真实业务材料。",
        encoding="utf-8",
    )
    (bundle_dir / "README.md").write_text(
        "\n".join(
            [
                f"# {bundle_name}",
                "",
                "这个目录用于放置真实业务语料评测集。",
                "",
                "建议流程：",
                "1. 将 `docs/` 中的占位文档替换为脱敏后的真实业务文档",
                "2. 修改 `corpus_manifest.json`，补全所有参与评测的文档",
                "3. 修改 `qa_dataset.json`，补到 25-30 题，包含 3-5 个拒答题",
                "4. 运行：",
                "   - `python eval/index_eval_corpus.py --bundle-dir <本目录> --pretty`",
                "   - `python eval/run_eval.py --bundle-dir <本目录> --user-id 9000 --pretty`",
                "   - `python eval/run_experiments.py --bundle-dir <本目录> --user-id 9000 --write-artifacts --pretty`",
            ]
        ),
        encoding="utf-8",
    )
    return {
        "bundle_dir": str(bundle_dir),
        "bundle_name": bundle_name,
        "files": [
            str(bundle_dir / "bundle_meta.json"),
            str(bundle_dir / "corpus_manifest.json"),
            str(bundle_dir / "qa_dataset.json"),
            str(bundle_dir / "experiment_matrix.json"),
            str(bundle_dir / "README.md"),
            str(docs_dir / "replace_with_real_document.md"),
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a scaffold for a real business RAG eval bundle")
    parser.add_argument("--bundle-name", required=True, help="Bundle directory name, for example contract_eval_2026q2")
    parser.add_argument("--output-root", default="eval/bundles", help="Root directory where bundles are created")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print output json")
    return parser.parse_args()


def main():
    args = parse_args()
    bundle_dir = Path(args.output_root) / args.bundle_name
    result = create_bundle(bundle_dir, args.bundle_name)
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
