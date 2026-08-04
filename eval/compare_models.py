"""AI-6: 模型/提示词对比决策工具。

在同一冻结评测集（generation_eval_dataset.json）上，对多个 LLM 模型各跑一遍
生成质量评测，输出逐模型通过率对比，用数据决定高价值场景是否值得换更贵模型。

用法（真实 LLM 调用，会消耗 API 额度；每次全量约 107+ 题）:
    python -B eval/compare_models.py --model qwen-plus --model qwen-max
    python -B eval/compare_models.py --model qwen-plus --model qwen-max \
        --api-base https://dashscope.aliyuncs.com/compatible-mode/v1

说明:
    - 每个模型在独立子进程中运行 eval/run_generation_eval.py（环境变量隔离），
      互不干扰，避免全局 settings 缓存串模型。
    - 默认不传 --no-llm（真实调用）；加 --no-llm 可对比确定性路径基线。
    - 输出逐模型 summary + 合并对比表（通过率/分类准确/拒答/法条失效层）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_once(model: str, *, no_llm: bool, api_base: str | None, dataset: str | None) -> dict:
    env = os.environ.copy()
    env["LLM_MODEL"] = model
    if api_base:
        env["LLM_API_BASE_URL"] = api_base
    command = [
        sys.executable, "-B", "eval/run_generation_eval.py",
        "--output", str(ROOT / "eval" / "outputs" / f"model_compare_{model}.json"),
    ]
    if no_llm:
        command.append("--no-llm")
    if dataset:
        command.extend(["--dataset", dataset])
    result = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"模型 {model} 评测失败 (rc={result.returncode}):\n{result.stderr[-2000:]}"
        )
    report_path = ROOT / "eval" / "outputs" / f"model_compare_{model}.json"
    return json.loads(report_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-6 模型对比评测")
    parser.add_argument("--model", action="append", required=True, help="要对比的模型名（可多次）")
    parser.add_argument("--api-base", type=str, default=None, help="覆盖 LLM_API_BASE_URL（可选）")
    parser.add_argument("--dataset", type=str, default=None, help="覆盖评测集路径（默认 107 题冻结集）")
    parser.add_argument("--no-llm", action="store_true", help="对比确定性路径基线（不消耗额度）")
    args = parser.parse_args()

    results: list[tuple[str, dict]] = []
    for model in args.model:
        print(f"\n=== 运行 {model} ...", file=sys.stderr)
        report = _run_once(model, no_llm=args.no_llm, api_base=args.api_base, dataset=args.dataset)
        results.append((model, report))

    print("\n=== 模型对比（生成质量评测） ===\n")
    print(f"{'模型':<20}{'总通过率':>10}{'总题数':>8}{'咨询分类':>10}{'拒答层':>10}{'法条失效层':>12}")
    print("-" * 74)
    for model, report in results:
        s = report["summary"]
        co = report["consultation"]
        print(
            f"{model:<20}{s['overall_pass_rate']:.1%}{s['total_cases']:>8}"
            f"{co['category_accuracy']:.1%}{co['refusal']['pass_rate']:.1%}"
            f"{co['inactive_source']['pass_rate']:.1%}"
        )
    print("\n详细报告: eval/outputs/model_compare_<model>.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
