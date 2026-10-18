#!/usr/bin/env python
"""固化 / 校验 RAG 评测 baseline 快照（90 天项：防回归）。

为什么需要它：`eval/run_experiments.py` 已经能跑实验矩阵、也能 `--check-regression` 比对，
但**快照本身从来没有被固化过**（`eval/outputs/baseline_snapshot.json` 一直缺失），
导致「评测与审计」页的 baseline 区块长期为空、也没有人能在改动后一键判断是否退化。
本脚本把这件事收成两个动作：`--write` 固化、`--check` 比对；并在快照里补上**锚点**
（git 提交、模型名、提示词版本），这样「参数 / prompt 版本锚定」是可核对的，而不是口头承诺。

用法：
    python -B scripts/eval_baseline.py --write                 # 跑一次 baseline 实验并固化快照
    python -B scripts/eval_baseline.py --check                 # 用已保存快照做回归比对（默认复用已有索引）
    python -B scripts/eval_baseline.py --check --with-index    # 连索引一起重建再比对（更慢、更严格）
    python -B scripts/eval_baseline.py --show                  # 只打印当前快照与锚点

退出码：0 = 通过；2 = 检出回归或配置漂移（可直接接进 CI / 发布门禁）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from eval.bundle_utils import (  # noqa: E402
    DEFAULT_BASELINE_SNAPSHOT_PATH,
    DEFAULT_DATASET_PATH,
    DEFAULT_MANIFEST_PATH,
    DEFAULT_MATRIX_PATH,
    DEFAULT_OUTPUT_DIR,
    load_bundle_meta,
    resolve_eval_paths,
)
from eval.common import ensure_eval_llm_ready, set_eval_seed  # noqa: E402
from eval.index_eval_corpus import load_manifest  # noqa: E402
from eval.run_eval import load_dataset  # noqa: E402
from eval.run_experiments import (  # noqa: E402
    build_baseline_snapshot,
    compare_with_baseline,
    load_matrix,
    run_experiments,
    write_experiment_outputs,
)

BASELINE_EXPERIMENT = "baseline"


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (out.stdout or "").strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _model_names() -> dict:
    """模型名从应用配置读取，避免快照里写死。"""
    try:
        from app.core.config import get_settings

        settings = get_settings()
        return {
            "llm_model": getattr(settings, "LLM_MODEL", None),
            "embedding_model": getattr(settings, "EMBEDDING_MODEL", None),
        }
    except Exception:  # pragma: no cover - 配置缺失时快照仍应能写出
        return {}


def _anchors(snapshot: dict) -> dict:
    config = ((snapshot.get("baseline") or {}).get("effective_config") or {})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "prompt_version": config.get("prompt_version"),
        "top_k": config.get("top_k"),
        "confidence_threshold": config.get("confidence_threshold"),
        "chunk_size": config.get("chunk_size"),
        "chunk_overlap": config.get("chunk_overlap"),
        **_model_names(),
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"Baseline snapshot must be an object: {path}")
    return data


def _print_anchors(snapshot: dict) -> None:
    anchors = snapshot.get("anchors") or {}
    if not anchors:
        print("  锚点：未记录（旧快照；重新 --write 会补上）")
        return
    print("  锚点：")
    for key in (
        "generated_at",
        "git_commit",
        "prompt_version",
        "top_k",
        "confidence_threshold",
        "chunk_size",
        "chunk_overlap",
        "llm_model",
        "embedding_model",
    ):
        if anchors.get(key) is not None:
            print(f"    {key} = {anchors[key]}")


def _print_summary(snapshot: dict) -> None:
    baseline = snapshot.get("baseline") or {}
    summary = baseline.get("summary") or {}
    print(f"  快照路径：{snapshot.get('_path')}")
    print(f"  baseline 实验：{snapshot.get('baseline_experiment')}（数据集 {snapshot.get('dataset_size')} 题）")
    print("  指标：")
    for key in (
        "hit_at_k",
        "mrr",
        "citation_accuracy",
        "refusal_accuracy",
        "retrieval_evidence_coverage",
        "citation_evidence_coverage",
        "badcase_count",
    ):
        if summary.get(key) is not None:
            print(f"    {key} = {summary[key]}")
    _print_anchors(snapshot)


def cmd_show(args: argparse.Namespace) -> int:
    path = Path(args.baseline_path)
    if not path.exists():
        print(f"快照不存在：{path}（先跑 --write）")
        return 1
    snapshot = _load_json(path)
    snapshot["_path"] = str(path)
    _print_summary(snapshot)
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    set_eval_seed(args.seed)
    ensure_eval_llm_ready()
    paths = resolve_eval_paths(
        bundle_dir=args.bundle_dir,
        manifest_path=args.manifest,
        dataset_path=args.dataset,
        matrix_path=args.matrix,
        output_dir=args.output_dir,
    )
    manifest = load_manifest(paths["manifest_path"])
    dataset = load_dataset(paths["dataset_path"])
    matrix = [item for item in load_matrix(paths["matrix_path"]) if (item.get("name") or BASELINE_EXPERIMENT) == BASELINE_EXPERIMENT]
    if not matrix:
        raise ValueError(f"Experiment '{BASELINE_EXPERIMENT}' not found in {paths['matrix_path']}")
    bundle_meta = load_bundle_meta(paths["bundle_dir"])

    result = run_experiments(
        manifest=manifest,
        dataset=dataset,
        matrix=matrix,
        user_id=args.user_id,
        skip_index=args.skip_index,
        bundle_meta=bundle_meta,
    )
    result["bundle_meta"] = bundle_meta
    artifacts = write_experiment_outputs(result, paths["output_dir"])
    snapshot = build_baseline_snapshot(result)
    snapshot["anchors"] = _anchors(snapshot)
    _write_json(Path(artifacts["baseline_snapshot_path"]), snapshot)
    snapshot["_path"] = artifacts["baseline_snapshot_path"]
    print("已固化 baseline 快照：")
    _print_summary(snapshot)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    set_eval_seed(args.seed)
    ensure_eval_llm_ready()
    path = Path(args.baseline_path)
    if not path.exists():
        print(f"快照不存在：{path}（先跑 --write 固化一次）")
        return 1
    snapshot = _load_json(path)
    paths = resolve_eval_paths(
        bundle_dir=args.bundle_dir,
        manifest_path=args.manifest,
        dataset_path=args.dataset,
        matrix_path=args.matrix,
        output_dir=args.output_dir,
    )
    manifest = load_manifest(paths["manifest_path"])
    dataset = load_dataset(paths["dataset_path"])
    matrix = [item for item in load_matrix(paths["matrix_path"]) if (item.get("name") or BASELINE_EXPERIMENT) == BASELINE_EXPERIMENT]
    if not matrix:
        raise ValueError(f"Experiment '{BASELINE_EXPERIMENT}' not found in {paths['matrix_path']}")
    bundle_meta = load_bundle_meta(paths["bundle_dir"])

    result = run_experiments(
        manifest=manifest,
        dataset=dataset,
        matrix=matrix,
        user_id=args.user_id,
        skip_index=not args.with_index,
        bundle_meta=bundle_meta,
    )
    result["bundle_meta"] = bundle_meta
    comparison = compare_with_baseline(result, snapshot)
    print("回归比对：")
    print(f"  baseline 实验：{comparison.get('baseline_experiment')}")
    print(f"  回归检出：{comparison.get('regression_detected')}")
    for key in ("metric_regressions", "badcase_regressions", "config_drift"):
        rows = comparison.get(key) or []
        if rows:
            print(f"  {key}（{len(rows)}）：")
            for row in rows:
                print(f"    {json.dumps(row, ensure_ascii=False)}")
    _print_anchors(snapshot)
    if comparison.get("regression_detected"):
        print("结论：检出回归或配置漂移（退出码 2）")
        return 2
    print("结论：与 baseline 一致（退出码 0）")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="固化 / 校验 RAG 评测 baseline 快照")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="跑一次 baseline 实验并固化快照")
    mode.add_argument("--check", action="store_true", help="用已保存快照做回归比对")
    mode.add_argument("--show", action="store_true", help="打印当前快照与锚点")
    parser.add_argument("--bundle-dir", default=None, help="评测 bundle 目录（含 manifest/dataset/matrix）")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX_PATH))
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="产物目录（默认 eval/outputs）")
    parser.add_argument("--baseline-path", default=str(DEFAULT_BASELINE_SNAPSHOT_PATH), help="快照路径")
    parser.add_argument("--user-id", type=int, default=9000, help="评测用的用户 id 过滤")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--skip-index", action="store_true", help="--write 时复用已有索引（默认重建）")
    parser.add_argument("--with-index", action="store_true", help="--check 时连索引一起重建（更慢、更严格）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.write:
        return cmd_write(args)
    if args.check:
        return cmd_check(args)
    return cmd_show(args)


if __name__ == "__main__":
    sys.exit(main())
