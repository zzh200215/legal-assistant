#!/usr/bin/env python
"""混合检索消融：同一语料、同一批问题，每次只改一个旋钮，看指标怎么动。

覆盖的旋钮（都是生产路径 `LegalRetrievalService.search` 真正读的配置）：
  · 词级 only（关掉稠密召回）——量化「加向量」到底带来了什么
  · RRF 融合常数 RAG_RRF_K（10 / 60 / 200）——词级与稠密两路的权重口径
  · 稠密召回候选数 LEGAL_DENSE_RECALL_MULTIPLIER × LEGAL_DENSE_MIN_CANDIDATES
  · LLM 重排开关 RAG_LLM_RERANK_ENABLED（多一次 LLM 往返，值不值）

指标与 `eval/run_hybrid_retrieval_eval.py` 一致（Hit@K / MRR / 拒答准确率 / 避让违规 / 延迟），
便于与既有报告对照。用法：

    python -B scripts/run_hybrid_retrieval_ablation.py                 # 全部臂
    python -B scripts/run_hybrid_retrieval_ablation.py --arms hybrid_rrf60,hybrid_llm_rerank
    python -B scripts/run_hybrid_retrieval_ablation.py --output eval/outputs/hybrid_retrieval_ablation.json

注意：需要真实 embedding（LLM_API_KEY），索引只建一次、各臂复用。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.config import get_settings  # noqa: E402
from eval.run_hybrid_retrieval_eval import (  # noqa: E402
    DEFAULT_CORPUS_PATH,
    DEFAULT_QA_PATH,
    build_db,
    build_service,
    load_json,
    run_eval_async,
)

settings = get_settings()

# 每个臂：名字 → (说明, settings 覆盖, 是否禁用稠密召回)
ARMS: dict[str, dict] = {
    "lexical_only": {
        "label": "词级 only（无向量）",
        "overrides": {},
        "disable_dense": True,
    },
    "hybrid_rrf60": {
        "label": "混合 RRF k=60（当前默认）",
        "overrides": {"RAG_RRF_K": 60},
        "disable_dense": False,
    },
    "hybrid_rrf10": {
        "label": "混合 RRF k=10",
        "overrides": {"RAG_RRF_K": 10},
        "disable_dense": False,
    },
    "hybrid_rrf200": {
        "label": "混合 RRF k=200",
        "overrides": {"RAG_RRF_K": 200},
        "disable_dense": False,
    },
    "hybrid_dense_10": {
        "label": "混合 + 稠密候选 10（默认 30）",
        "overrides": {"LEGAL_DENSE_MIN_CANDIDATES": 10, "LEGAL_DENSE_RECALL_MULTIPLIER": 1},
        "disable_dense": False,
    },
    "hybrid_llm_rerank": {
        "label": "混合 + LLM 重排 top5",
        "overrides": {"RAG_LLM_RERANK_ENABLED": True, "RAG_LLM_RERANK_TOP_N": 5},
        "disable_dense": False,
    },
}

TRACKED = ("hit_at_5", "mrr", "refusal_accuracy", "avoid_source_violations")


class _NoEmbedClient:
    """代理真实 client，只让 embed 失败——稠密召回因此静默降级为词级（生产同款降级路径）。"""

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def embed(self, *args, **kwargs):
        raise RuntimeError("dense recall disabled for ablation")


def _apply(overrides: dict, saved: dict) -> None:
    for key, value in overrides.items():
        if key not in saved:
            saved[key] = getattr(settings, key)
        setattr(settings, key, value)


def _restore(saved: dict) -> None:
    for key, value in saved.items():
        setattr(settings, key, value)


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT_DIR), capture_output=True, text=True, timeout=10)
        return (out.stdout or "").strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _markdown(report: dict) -> str:
    lines = [
        "| 臂 | Hit@5 | MRR | 拒答准确率 | 避让违规 | badcase | 耗时(s) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm in report["arms"]:
        summary = arm["summary"]
        lines.append(
            "| {label} | {hit} | {mrr} | {refusal} | {avoid} | {bad} | {secs} |".format(
                label=arm["label"],
                hit=summary.get("hit_at_5"),
                mrr=summary.get("mrr"),
                refusal=summary.get("refusal_accuracy"),
                avoid=summary.get("avoid_source_violations"),
                bad=len(summary.get("badcases") or []),
                secs=arm["elapsed_seconds"],
            )
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="法律混合检索消融")
    parser.add_argument("--corpus-path", default=str(DEFAULT_CORPUS_PATH))
    parser.add_argument("--qa-path", default=str(DEFAULT_QA_PATH))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--arms", default="", help="逗号分隔的臂名；默认全部")
    parser.add_argument("--output", default=None, help="JSON 产物路径")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    selected = [name.strip() for name in args.arms.split(",") if name.strip()] or list(ARMS)
    unknown = [name for name in selected if name not in ARMS]
    if unknown:
        raise SystemExit(f"未知臂：{unknown}；可选：{list(ARMS)}")

    corpus = load_json(Path(args.corpus_path))
    qa_dataset = load_json(Path(args.qa_path))
    db, corpus_id_to_source_id = build_db(corpus)
    service = build_service(index_embeddings=True, db=db)  # 索引只建一次，各臂复用
    real_client = service.client

    arms_report = []
    baseline_ranks: dict[str, list[int]] = {}
    try:
        for name in selected:
            spec = ARMS[name]
            saved: dict = {}
            _apply(spec["overrides"], saved)
            service.client = _NoEmbedClient(real_client) if spec["disable_dense"] else real_client
            started = time.time()
            summary = asyncio.run(run_eval_async(qa_dataset, db, service, corpus_id_to_source_id, args.top_k))
            elapsed = round(time.time() - started, 2)
            service.client = real_client
            _restore(saved)

            ranks = {
                case["name"]: case["ranked_source_ids_top_k"]
                for case in summary.get("cases") or []
            }
            if name == "hybrid_rrf60":
                baseline_ranks = ranks
            moved = []
            if baseline_ranks:
                for case_name, ranked in ranks.items():
                    base = baseline_ranks.get(case_name)
                    if base is not None and base != ranked:
                        moved.append({"case": case_name, "default": base, "arm": ranked})

            arms_report.append(
                {
                    "name": name,
                    "label": spec["label"],
                    "overrides": spec["overrides"],
                    "disable_dense": spec["disable_dense"],
                    "elapsed_seconds": elapsed,
                    "summary": {key: summary.get(key) for key in TRACKED} | {"badcases": summary.get("badcases")},
                    "rank_changes_vs_default": moved,
                }
            )
            print(f"[{name}] {spec['label']} → hit@5={summary.get('hit_at_5')} mrr={summary.get('mrr')} "
                  f"refusal={summary.get('refusal_accuracy')} avoid={summary.get('avoid_source_violations')} "
                  f"badcases={len(summary.get('badcases') or [])} ({elapsed}s)")
    finally:
        db.close()

    report = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "top_k": args.top_k,
        "corpus_size": len(corpus),
        "qa_size": len(qa_dataset),
        "embedding_model": getattr(settings, "EMBEDDING_MODEL", None),
        "llm_model": getattr(settings, "LLM_MODEL", None),
        "service": "LegalRetrievalService.search（生产混合检索路径）",
        "arms": arms_report,
    }
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n产物：{out_path}")
    print()
    print(_markdown(report))
    if args.pretty:
        print()
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
