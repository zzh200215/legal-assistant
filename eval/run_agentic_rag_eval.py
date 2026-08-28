"""Agentic-RAG 消融评测：多跳分解 / 判分驱动的补检索，各自带来多少证据覆盖率。

衡量对象是**交给生成节点的证据集合**，不是答案质量——答案要评就得真调 LLM，那不属于
这个脚本。三条臂共用同一份确定性嵌入（复用 run_document_rag_eval 的字符双字袋，两个评测
的数字因此可比）与真实混合检索管线；分解与判分是脚本桩（真实实现是 LLM 往返），桩只决定
「什么时候再查一轮 / 拆成哪几个子问题」，覆盖率的变化来自真实检索。

    single_hop                原问题检索一次（基线）
    multi_hop                 按数据集给定的子问题扇出，合并去重后的证据
    single_hop_judge_refine   判分器第一轮判不足并给出 missing，第二轮补检索

默认扫 top_k=1/2/3 而不是只报一个点：语料只有 4 篇文档，top_k>=2 时单跳基线本身就已经
覆盖了全部必需证据（``baseline_saturated``），消融在那些点上无区分度——只报一个宽 top_k
会得出「多跳无用」，只报 top_k=1 又是挑对自己有利的点。差异出现在 top_k=1，那也是真实
库里 top_k 相对语料规模偏紧时的情形。

用法：
    python -B eval/run_agentic_rag_eval.py --pretty
    python -B eval/run_agentic_rag_eval.py --top-k 1 --output eval/outputs/agentic_rag_ablation.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).resolve().parent
for path in (ROOT_DIR, EVAL_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import chromadb

import app.services.rag.agentic_rag_service as agentic_module
import app.services.rag.rag_service as rag_module
from app.services.documents.document_parsing import _split_text
from app.services.rag.agentic_rag_service import AgenticRAGService
from app.services.rag.vector_store import ChromaVectorStoreCollection
from run_document_rag_eval import _embed, load_json

DEFAULT_CASES_PATH = EVAL_DIR / "agentic_rag_cases.json"
DEFAULT_CORPUS_PATH = EVAL_DIR / "document_rag_corpus.json"

# 每条臂只改这几个开关，其余保持默认；planner 与忠实性校验全程关闭（都要真 LLM）。
ARM_SETTINGS: dict[str, dict[str, bool]] = {
    "single_hop": {"AGENTIC_RAG_MULTI_HOP_ENABLED": False, "AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED": False},
    "multi_hop": {"AGENTIC_RAG_MULTI_HOP_ENABLED": True, "AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED": False},
    "single_hop_judge_refine": {"AGENTIC_RAG_MULTI_HOP_ENABLED": False, "AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED": True},
}


def build_pipeline(corpus: list[dict], searched: list[str]) -> Any:
    """把 RAG 单例接到临时 collection 上，并给检索计一次数（成本列要用）。

    改的是模块单例而不是新实例：agentic 图的节点用的就是这个单例。
    """
    rag_module.llm_client.embed = _embed
    rag = rag_module.rag_service
    rag.collection = ChromaVectorStoreCollection(
        chromadb.EphemeralClient().get_or_create_collection("eval_agentic_rag")
    )
    rag._bm25_stale = True
    rag._reranker = None
    for doc in corpus:
        chunks = _split_text(doc["content"], chunk_size=120, chunk_overlap=20)
        for chunk in chunks:
            chunk["id"] = None
            chunk["embedding_id"] = f"doc{doc['id']}_chunk{chunk['chunk_index']}"
        rag.index_document(doc["id"], chunks, user_id=1)

    real_search = rag.search_async

    async def counted_search(query: str, **kwargs: Any) -> list[dict]:
        searched.append(query)
        return await real_search(query, **kwargs)

    rag.search_async = counted_search
    # 生成节点要真 LLM，这里换成拦截器：评测只看送进生成的证据是哪些。
    captured: dict[str, list[dict]] = {}

    async def capture_answer(query: str, *, chunks: list[dict], **kwargs: Any) -> dict:
        captured["chunks"] = list(chunks)
        return {
            "answer": "(eval stub)",
            "citations": [],
            "confidence": 0.8,
            "can_answer": True,
            "refusal_reason": None,
            "hit_chunks": list(chunks),
            "context_chunks": list(chunks),
            "latency_ms": 0,
            "observability": {},
        }

    rag.answer_from_chunks_async = capture_answer
    agentic_module.llm_observability_service.log_event = lambda **kwargs: None
    return rag, captured


@contextmanager
def arm_settings(service: AgenticRAGService, arm: str):
    overrides = {
        "AGENTIC_RAG_PLANNER_ENABLED": False,
        "AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED": False,
        **ARM_SETTINGS[arm],
    }
    previous = {key: getattr(service.settings, key) for key in overrides}
    for key, value in overrides.items():
        setattr(service.settings, key, value)
    try:
        yield
    finally:
        for key, value in previous.items():
            setattr(service.settings, key, value)


@contextmanager
def stubbed_judges(case: dict):
    """脚本桩替掉两次 LLM 往返：分解按数据集给定，判分第一轮判不足并给出 missing。"""
    real = (agentic_module.decompose_question, agentic_module.judge_evidence)
    rounds = {"judge": 0}

    async def decompose(question: str, *, max_sub_questions: int, user_id: int | None = None) -> dict:
        return {"available": True, "sub_questions": list(case["sub_questions"])[:max_sub_questions]}

    async def judge(question: str, chunks: list[dict], *, user_id: int | None = None) -> dict:
        rounds["judge"] += 1
        first_round = rounds["judge"] == 1
        return {
            "available": True,
            "sufficient": not first_round,
            "score": 0.4 if first_round else 0.9,
            "missing": case["missing_hint"] if first_round else "",
        }

    agentic_module.decompose_question = decompose
    agentic_module.judge_evidence = judge
    try:
        yield
    finally:
        agentic_module.decompose_question, agentic_module.judge_evidence = real


def coverage(chunks: list[dict], must_include: list[dict]) -> list[bool]:
    """每条必需证据是否出现在送进生成的片段里（按文档 + 片段原文匹配）。"""
    matched = []
    for item in must_include:
        matched.append(
            any(
                (chunk.get("metadata") or {}).get("document_id") == item["document_id"]
                and item["match"] in (chunk.get("content") or "")
                for chunk in chunks
            )
        )
    return matched


async def run_arm(
    service: AgenticRAGService,
    cases: list[dict],
    *,
    arm: str,
    top_k: int,
    searched: list[str],
    captured: dict[str, list[dict]],
) -> dict:
    case_reports = []
    with arm_settings(service, arm):
        for case in cases:
            searched.clear()
            captured.clear()
            with stubbed_judges(case):
                result = await service.answer_async(case["question"], user_id=1, top_k=top_k)
            matched = coverage(captured.get("chunks") or [], case["must_include"])
            case_reports.append(
                {
                    "name": case["name"],
                    "coverage": round(sum(matched) / len(matched), 4),
                    "missed": [
                        item["match"] for item, hit in zip(case["must_include"], matched, strict=True) if not hit
                    ],
                    "evidence_chunks": len(captured.get("chunks") or []),
                    "retrieval_calls": len(searched),
                    "rounds": result["agentic_rag"]["retrieval_rounds"],
                    "nodes": [step["node"] for step in result["agentic_rag"]["steps"]],
                }
            )
    total = len(case_reports) or 1
    return {
        "evidence_coverage": round(sum(item["coverage"] for item in case_reports) / total, 4),
        "full_coverage_rate": round(sum(item["coverage"] == 1.0 for item in case_reports) / total, 4),
        "avg_retrieval_calls": round(sum(item["retrieval_calls"] for item in case_reports) / total, 4),
        "avg_rounds": round(sum(item["rounds"] for item in case_reports) / total, 4),
        "cases": case_reports,
    }


async def run_eval(cases: list[dict], corpus: list[dict], *, top_ks: list[int]) -> dict:
    searched: list[str] = []
    _, captured = build_pipeline(corpus, searched)
    service = AgenticRAGService()
    sweep: dict[str, Any] = {}
    for top_k in top_ks:
        arms = {
            arm: await run_arm(service, cases, arm=arm, top_k=top_k, searched=searched, captured=captured)
            for arm in ARM_SETTINGS
        }
        baseline = arms["single_hop"]["evidence_coverage"]
        sweep[str(top_k)] = {
            "arms": arms,
            "coverage_lift": {
                arm: round(report["evidence_coverage"] - baseline, 4)
                for arm, report in arms.items()
                if arm != "single_hop"
            },
            # 语料只有 4 篇文档：top_k 一放宽，单跳基线就已经覆盖全部必需证据，
            # 消融在该点上无区分度。扫一遍 top_k 而不是只报一个点，就是为了让这件事看得见。
            "baseline_saturated": baseline >= 1.0,
        }
    return {
        "eval": "agentic_rag_ablation",
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_deterministic_embedding_with_stubbed_judges",
        "total_cases": len(cases),
        "top_k_sweep": top_ks,
        "sweep": sweep,
        "limitations": "只衡量送进生成节点的证据覆盖率；分解与判分为脚本桩，真实实现各多一次 LLM 往返。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic RAG 多跳/判分补检索消融评测（离线确定性）")
    parser.add_argument("--cases-path", default=str(DEFAULT_CASES_PATH))
    parser.add_argument("--corpus-path", default=str(DEFAULT_CORPUS_PATH))
    parser.add_argument("--top-k", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--output", default=None)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    cases = load_json(Path(args.cases_path))
    corpus = load_json(Path(args.corpus_path))
    report = asyncio.run(run_eval(cases, corpus, top_ks=list(args.top_k)))
    text = json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        print(f"报告已写入 {output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
