#!/usr/bin/env python
"""打印「可溯源」的作品集指标：所有数字都从 eval/outputs 的评测产物与构建产物里现读，不手抄。

用法：
    python -B scripts/demo_metrics.py                # 只读评测产物
    python -B scripts/demo_metrics.py --with-bundle  # 额外跑一次前端包体预算检查（需要 frontend/dist 已构建）

为什么要有这个脚本：简历/演示里出现的每个数字都应该能被一条命令复现，而不是从某次运行里抄下来的。
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "eval" / "outputs"


def load(name: str) -> dict:
    path = OUTPUTS / name
    if not path.exists():
        return {}
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pct(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value * 100:.2f}%"
    return "—"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-bundle", action="store_true", help="额外运行前端包体预算检查")
    args = parser.parse_args()

    print("=" * 68)
    print("第一层：规则层（确定性指标，无模型参与）")
    print("=" * 68)
    rule = load("qa_eval_repaired.json")
    summary = rule.get("summary") or {}
    if summary:
        print(f"  来源：eval/outputs/qa_eval_repaired.json（evaluation_id={rule.get('evaluation_id')}）")
        print(f"  用例：{summary.get('total_cases')} 题（可答 {summary.get('answerable_cases')} / 拒答 {summary.get('refusal_cases')}）")
        print(f"  检索 hit@k={pct(summary.get('hit_at_k'))} mrr={pct(summary.get('mrr'))} ndcg@5={pct(summary.get('ndcg_at_5'))}")
        print(f"  证据覆盖率 retrieval={pct(summary.get('retrieval_evidence_coverage'))} citation={pct(summary.get('citation_evidence_coverage'))}")
        print(f"  引用准确率={pct(summary.get('citation_accuracy'))} 拒答准确率={pct(summary.get('refusal_accuracy'))}")
        print(f"  延迟 mean={summary.get('average_latency_ms')}ms p50={summary.get('latency_p50_ms')}ms p95={summary.get('latency_p95_ms')}ms")
        print(f"  平均检索轮次={summary.get('average_retrieval_rounds')} badcase={summary.get('badcase_count')}")
    else:
        print("  未找到评测产物（先跑 eval/run_eval.py 或 eval/run_agentic_rag_eval.py）")

    print()
    print("=" * 68)
    print("第二层：跨模型裁判层（忠实度 + 三维质量）")
    print("=" * 68)
    judge = load("cross_judge_report.json")
    jsummary = judge.get("summary") or {}
    if jsummary:
        faith = jsummary.get("faithfulness") or {}
        quality = jsummary.get("quality") or {}
        print(f"  来源：eval/outputs/cross_judge_report.json（生成于 {judge.get('generated_at')}）")
        for side in ("glm", "qwen-plus", "judge_a", "judge_b"):
            if side in faith or side in quality:
                pass
        print(f"  忠实度：{json.dumps(faith, ensure_ascii=False)}")
        print(f"  三维质量：{json.dumps(quality, ensure_ascii=False)}")
    else:
        print("  未找到跨模型裁判报告（先跑 scripts/run_cross_judge.py）")

    faith_report = load("faithfulness_report.json")
    fsummary = faith_report.get("summary") or {}
    if fsummary:
        print(f"  单模型忠实度报告：eval/outputs/faithfulness_report.json（judge={faith_report.get('judge_model')}）")
        print(f"    忠实度率={pct(fsummary.get('faithfulness_rate'))}（{fsummary.get('faithful_cases')}/{fsummary.get('judged')}）")

    if args.with_bundle:
        print()
        print("=" * 68)
        print("前端包体预算（npm run bundle:check 现跑）")
        print("=" * 68)
        frontend = ROOT / "frontend"
        if not (frontend / "dist").exists():
            print("  跳过：frontend/dist 不存在（先 npm run build）")
        else:
            proc = subprocess.run(
                ["node", "scripts/check-bundle-budget.mjs"],
                cwd=str(frontend),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in (proc.stdout or "").strip().splitlines()[-6:]:
                print(f"  {line}")
            print(f"  退出码：{proc.returncode}（0 = 四项预算全过）")

    print()
    print("=" * 68)
    print("可复现口径提醒")
    print("=" * 68)
    print("  · 评测数字：跑 eval/run_eval.py / eval/run_agentic_rag_eval.py 后重跑本脚本")
    print("  · 裁判数字：scripts/run_cross_judge.py --from-report <上一层报告>")
    print("  · 测试数量：见 docs/review/step13-three-layer-review.md（含每条命令）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
