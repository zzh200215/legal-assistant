"""评测集 PII 校验（阶段 4）：脱敏前后对比 + 残留检测。

- 输入：文本字符串（多行）或评测 JSONL/JSON（qa_dataset / bundle 文件）。
- 校验：对每条文本运行 ``detect_pii``，命中即失败（fail-closed）。
- 退出码：0=无 PII 残留；1=发现残留；2=用法/输入错误。

用法：
  python -B eval/redact_check.py --text "张三 13800138000"
  python -B eval/redact_check.py --dataset eval/generation_eval_dataset.json
  python -B eval/redact_check.py --dir eval/bundles/demo_legal
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from eval.redact import detect_pii, redact_pii


def _collect_texts(payload: object) -> list[str]:
    """从评测数据结构中采集应脱敏的文本字段。"""
    texts: list[str] = []

    def walk(node):
        if isinstance(node, str):
            texts.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(payload)
    return texts


def check_texts(texts: list[str]) -> list[dict]:
    residuals = []
    for index, text in enumerate(texts):
        if not text or len(text) < 4:
            continue
        hits = detect_pii(text)
        if hits:
            residuals.append({"index": index, "hits": hits, "snippet": text[:80]})
    return residuals


def main() -> int:
    parser = argparse.ArgumentParser(description="PII residual check for eval datasets")
    parser.add_argument("--text", default=None, help="Raw text to scan")
    parser.add_argument("--dataset", default=None, help="JSON file (dataset / bundle payload)")
    parser.add_argument("--dir", default=None, help="Directory to scan all *.json")
    args = parser.parse_args()

    texts: list[str] = []
    if args.text:
        texts = [args.text]
    elif args.dataset:
        with Path(args.dataset).open("r", encoding="utf-8") as file:
            texts = _collect_texts(json.load(file))
    elif args.dir:
        for path in sorted(Path(args.dir).rglob("*.json")):
            try:
                with path.open("r", encoding="utf-8") as file:
                    texts.extend(_collect_texts(json.load(file)))
            except json.JSONDecodeError:
                continue
    else:
        parser.print_help()
        return 2

    residuals = check_texts(texts)
    if residuals:
        print(json.dumps({"status": "fail", "residual_count": len(residuals), "residuals": residuals}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({"status": "ok", "checked": len(texts), "residual_count": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # 示例：脱敏后再校验应通过
    demo = redact_pii("张三电话 13800138000，身份证 110101199001011234")
    demo_check = detect_pii(demo)
    if demo_check:
        print(f"redact_pii 校验演示未通过: {demo_check}", file=sys.stderr)
    sys.exit(main())
