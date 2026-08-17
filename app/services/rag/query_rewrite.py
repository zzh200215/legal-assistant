"""查询改写簇：规则改写（filler 清洗 + 聚焦 + 术语扩展）与 LLM 改写。

这些方法原本是 ``RAGService`` 上的查询改写方法，以 mixin 形式抽离以削减上帝类规模。
它们依赖 ``self._normalize_query``/``self._extract_query_units``/``self._should_use_llm_rewrite``/
``self._parse_llm_json`` 等委托方法（定义于 ``RAGService``），行为不变。
"""

import logging
import re

from app.core.config import get_settings
from app.core.llm_client import llm_client
from app.services.rag.query_expansion import expand_terms

settings = get_settings()
logger = logging.getLogger(__name__)


class QueryRewriteMixin:
    def _rewrite_queries(self, query: str, limit: int | None = None) -> list[str]:
        normalized = self._normalize_query(query)
        if not normalized:
            return []

        variants: list[str] = [normalized]
        simplified = normalized
        filler_patterns = [
            r"^(请问|帮我|麻烦你|麻烦|请帮我)",
            r"(一下|看看|说明一下|告诉我)$",
            r"(这份文档里|文档里|文档中|材料里|材料中)",
        ]
        for pattern in filler_patterns:
            simplified = re.sub(pattern, "", simplified)
        simplified = self._normalize_query(re.sub(r"[？?。!！,，：:]", " ", simplified))
        if simplified and simplified not in variants:
            variants.append(simplified)

        compact = re.sub(r"\s+", "", simplified)
        if compact and compact not in variants:
            variants.append(compact)

        key_terms = sorted(self._extract_query_units(simplified), key=len, reverse=True)
        if key_terms:
            focused = " ".join(key_terms[:4])
            if focused and focused not in variants:
                variants.append(focused)

        # Query Expansion：法律术语同义扩展，扩大混合召回覆盖面
        if settings.RAG_QUERY_EXPANSION_ENABLED:
            expanded = expand_terms(normalized)
            if expanded:
                expanded_query = normalized + " " + " ".join(expanded[: settings.RAG_QUERY_EXPANSION_MAX])
                if expanded_query not in variants:
                    variants.append(expanded_query)

        unique_variants: list[str] = []
        for variant in variants:
            cleaned = self._normalize_query(variant)
            if cleaned and cleaned not in unique_variants:
                unique_variants.append(cleaned)
        final_limit = max(1, int(limit if limit is not None else settings.RAG_QUERY_VARIANT_LIMIT))
        return unique_variants[:final_limit]

    async def _rewrite_query_llm(self, query: str, user_id: int | None = None) -> list[str]:
        """LLM 查询改写：产出检索表达式 + 扩展词；失败/关闭时返回空（保持规则改写）。"""
        if not settings.RAG_QUERY_REWRITE_LLM_ENABLED or not self._should_use_llm_rewrite(query):
            return []
        prompt = (
            "你是法律文档检索优化器。把问题改写为更利于检索的表达式，并补充同义/相关检索词，"
            "不回答问题、不添加事实。\n"
            "只输出 JSON：{\"search_query\": \"不超过300字的检索表达式\", \"expand\": [\"词1\", \"词2\"]}\n"
            f"原始问题：{query}"
        )
        try:
            raw = await llm_client.generate(prompt, temperature=0.0, action="rag_query_rewrite", user_id=user_id)
            payload = self._parse_llm_json(raw)
            variants: list[str] = []
            search_query = str(payload.get("search_query") or "").strip()
            if search_query and len(search_query) <= 300:
                variants.append(search_query)
            expand = payload.get("expand") or []
            for term in expand[: settings.RAG_QUERY_EXPANSION_MAX]:
                term = str(term).strip()
                if term and term not in query:
                    variants.append(query + " " + term)
            return [v for v in variants if v]
        except Exception as exc:  # noqa: BLE001 - 改写失败回退规则
            logger.warning("LLM query rewrite failed; keeping rule variants (%s)", type(exc).__name__)
            return []
