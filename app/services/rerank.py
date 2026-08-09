"""RAG④ 可插拔重排：启发式默认 + 可选 LLM 重排（qwen-plus 打分，失败回退启发式）。

用法：`build_reranker(rag_service)` 返回 Reranker，search_async 委托其 rerank。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from typing import Any, Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class Reranker(ABC):
    @abstractmethod
    async def rerank(
        self,
        *,
        query: str,
        query_variants: list[str],
        candidates: list[dict],
        top_k: int,
        user_id: Optional[int] = None,
    ) -> list[dict]:
        ...


class HeuristicReranker(Reranker):
    """委托现有启发式权重重排（默认，行为不变）。"""

    def __init__(self, service: Any):
        self._service = service

    async def rerank(self, *, query, query_variants, candidates, top_k, user_id=None) -> list[dict]:
        return self._service._rerank_candidates(
            query=query,
            query_variants=query_variants,
            fused_candidates=candidates,
            top_k=top_k,
        )


class LLMReranker(Reranker):
    """LLM 重排：先启发式排序取 top-N，再用 qwen-plus 打分重排；异常/解析失败回退启发式。"""

    def __init__(self, service: Any):
        self._service = service
        self._heuristic = HeuristicReranker(service)

    async def rerank(self, *, query, query_variants, candidates, top_k, user_id=None) -> list[dict]:
        if not candidates:
            return []
        from app.core.llm_client import llm_client

        ordered = await self._heuristic.rerank(
            query=query, query_variants=query_variants,
            candidates=candidates, top_k=len(candidates), user_id=user_id,
        )
        top_n = ordered[: settings.RAG_LLM_RERANK_TOP_N]
        if not top_n:
            return ordered[:top_k]
        try:
            prompt = self._build_prompt(query, top_n)
            response = await llm_client.generate(
                prompt, temperature=0.0, action="rag_rerank", user_id=user_id,
            )
            scores = self._parse_scores(response)
            ranked = self._apply_scores(top_n, scores)
            return (ranked + ordered[len(top_n):])[:top_k]
        except Exception as exc:  # noqa: BLE001 - 重排失败不阻断主链路
            logger.warning("LLM rerank failed; falling back to heuristic (%s)", type(exc).__name__)
            return ordered[:top_k]

    def _build_prompt(self, query: str, candidates: list[dict]) -> str:
        lines = []
        for index, candidate in enumerate(candidates):
            snippet = (candidate.get("content") or "")[: settings.RAG_LLM_RERANK_MAX_CHARS]
            lines.append(f"[{index}] {snippet}")
        return (
            "你是法律检索相关性评判。请按与问题的相关程度给每个片段打分（0-10 整数，越高越相关）。"
            "只输出 JSON：{\"scores\":[<int>,...]}，不要输出其他内容。\n"
            f"问题：{query}\n"
            + "\n".join(lines)
        )

    @staticmethod
    def _parse_scores(response: str) -> list[int]:
        text = response.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise ValueError("LLM 重排响应缺少 JSON")
        data = json.loads(text[start:end + 1])
        scores = data.get("scores")
        if not isinstance(scores, list):
            raise ValueError("LLM 重排响应缺少 scores 数组")
        return [int(score) for score in scores]

    @staticmethod
    def _apply_scores(candidates: list[dict], scores: list[int]) -> list[dict]:
        scored = [(index, candidate, scores[index] if index < len(scores) else 0)
                  for index, candidate in enumerate(candidates)]
        scored.sort(key=lambda item: item[2], reverse=True)
        return [{**candidate, "llm_rerank_score": score}
                for _, candidate, score in scored]


class BGEReranker(Reranker):
    """BGE 交叉编码器重排（BAAI/bge-reranker-v2-m3）。

    模型懒加载（sentence-transformers CrossEncoder），未安装依赖/模型加载失败时回退启发式。
    模型权重经 HF 镜像下载（HF_ENDPOINT 缺省指向 hf-mirror.com）。
    """
    _model = None
    _model_lock = threading.Lock()

    def __init__(self, service: Any):
        self._service = service
        self._heuristic = HeuristicReranker(service)

    @classmethod
    def _load_model(cls):
        if cls._model is not None:
            return cls._model
        with cls._model_lock:
            if cls._model is not None:
                return cls._model
            try:
                os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
                from sentence_transformers import CrossEncoder
                cls._model = CrossEncoder(settings.RAG_RERANK_MODEL)
            except Exception as exc:  # noqa: BLE001
                logger.warning("BGE reranker model unavailable; falling back (%s)", type(exc).__name__)
                cls._model = None
        return cls._model

    async def rerank(self, *, query, query_variants, candidates, top_k, user_id=None) -> list[dict]:
        if not candidates:
            return []
        if self._load_model() is None:
            return await self._heuristic.rerank(
                query=query, query_variants=query_variants, candidates=candidates,
                top_k=top_k, user_id=user_id,
            )
        ordered = await self._heuristic.rerank(
            query=query, query_variants=query_variants, candidates=candidates,
            top_k=len(candidates), user_id=user_id,
        )
        top_n = ordered[: settings.RAG_RERANK_TOP_N]
        if len(top_n) < 2:
            return ordered[:top_k]
        try:
            pairs = [(query, (candidate.get("content") or "")[: settings.RAG_RERANK_MAX_CHARS])
                     for candidate in top_n]
            scores = await asyncio.to_thread(self._score, pairs)
            ranked = sorted(zip(top_n, scores), key=lambda item: item[1], reverse=True)
            result = []
            for candidate, score in ranked:
                item = dict(candidate)
                item["bge_rerank_score"] = round(float(score), 6)
                result.append(item)
            return (result + ordered[len(top_n):])[:top_k]
        except Exception as exc:  # noqa: BLE001
            logger.warning("BGE rerank failed; falling back to heuristic (%s)", type(exc).__name__)
            return ordered[:top_k]

    @staticmethod
    def _score(pairs: list[tuple[str, str]]) -> list[float]:
        model = BGEReranker._model
        scores = model.predict(pairs)
        # predict 可能返回 numpy 数组/list/标量
        if hasattr(scores, "__len__") and not isinstance(scores, (str, bytes)):
            return [float(s) for s in scores]
        return [float(scores)]


def build_reranker(service: Any) -> Reranker:
    engine = settings.RAG_RERANK_ENGINE
    if settings.RAG_LLM_RERANK_ENABLED:
        engine = "llm"  # 兼容旧开关：显式开启 LLM 重排
    if engine == "bge":
        return BGEReranker(service)
    if engine == "llm":
        return LLMReranker(service)
    return HeuristicReranker(service)
