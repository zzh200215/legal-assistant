"""Controlled Agentic RAG orchestration built on top of the existing RAG service.

The graph is intentionally bounded: it may refine a retrieval query once, but
never bypasses document permissions, citation grounding, or refusal checks in
``RAGService``.

问题分解后的每个子问题是图上的一个 ``Send`` 分支（一个独立图任务），证据判分与生成后
忠实性校验则是图上的独立节点——都可以在 checkpoint 里逐步回放，而不是藏在某个节点内部。
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any, TypedDict

from app.core.async_utils import run_async
from app.core.config import get_settings
from app.core.telemetry import observe_span
from app.services.llm.llm_observability_service import llm_observability_service
from app.services.llm.llm_service import llm_service
from app.services.rag.agentic_judge import check_faithfulness, decompose_question, judge_evidence
from app.services.rag.rag_service import rag_service
from app.services.rag.trace import get_last_trace
from app.workflows.langgraph_compat import (
    GRAPH_END,
    GRAPH_START,
    Send,
    StateGraph,
    build_checkpointer,
    merge_keyed_slots,
    workflow_engine_name,
)

# 多跳门槛：只有问题里真的像有多个子问题，才值得付分解那一次 LLM 往返。
_MULTI_HOP_MARKERS = ("以及", "并且", "同时", "分别", "还有", "另外", "；", ";")

# 忠实性校验不通过时的拒答口径：沿用 RAGService 的拒答措辞风格，只换 refusal_reason。
_UNFAITHFUL_ANSWER = "根据当前文档内容，暂时无法给出可核对到片段的确定答案。"
_UNFAITHFUL_REFUSAL = "unfaithful_generation"


class AgenticRAGState(TypedDict, total=False):
    """Typed state channels for the bounded agentic-RAG graph.

    Explicit schema (vs a bare ``dict``) documents the contract between nodes and
    lets LangGraph validate/route on named channels.
    """

    question: str
    active_query: str
    document_id: int | None
    user_id: int | None
    knowledge_base_id: int | None
    document_status: str | None
    authorized_document_ids: list[int] | None
    conversation_history: list[dict] | None
    runtime_config: dict[str, Any]
    max_rounds: int
    round: int
    retrieval_duration_ms: int
    latest_chunks: list[Any]
    latest_confidence: float
    best_chunks: list[Any]
    best_confidence: float
    # 每轮检索的证据快照：生成节点要把各轮并起来，只留「最好的一轮」会丢掉补检索的收益。
    round_evidence: list[dict[str, Any]]
    evidence_ready: bool
    latest_retrieval_trace: dict[str, Any] | None
    trace: list[dict[str, Any]]
    started: float
    result: dict[str, Any] | None
    sub_questions: list[str]
    # 子问题分支并发写同一通道，必须走 reducer；键是子问题序号，值是该分支的检索结果。
    sub_results: Annotated[dict[str, dict[str, Any]], merge_keyed_slots]
    evidence_judgement: dict[str, Any] | None
    faithfulness: dict[str, Any] | None


class AgenticRAGService:
    # ``Send`` 分支只拿到载荷、拿不到整份 state，权限范围键必须逐个随载荷带过去：
    # 漏一个，那个分支就等于放开了对应的过滤。
    _SCOPE_KEYS = ("document_id", "user_id", "knowledge_base_id", "document_status", "authorized_document_ids")

    def __init__(self) -> None:
        self.settings = get_settings()
        self._workflow = self._build_workflow()

    @staticmethod
    def _should_use_model_planner(query: str) -> bool:
        normalized = query or ""
        complex_markers = ("比较", "对比", "差异", "风险", "条件", "流程", "哪些", "分别", "以及", "和")
        return len(normalized) >= 24 or any(marker in normalized for marker in complex_markers)

    @staticmethod
    def _looks_multi_hop(question: str) -> bool:
        """便宜的前置门槛：问了两件事才分解，避免为单问题白付一次 LLM 往返。"""
        normalized = (question or "").strip()
        if len(normalized) < 16:
            return False
        if normalized.count("？") + normalized.count("?") >= 2:
            return True
        return any(marker in normalized for marker in _MULTI_HOP_MARKERS)

    async def _plan_query(
        self,
        query: str,
        *,
        user_id: int | None,
        refinement: bool = False,
        missing_hint: str = "",
    ) -> tuple[str, str]:
        if not self.settings.AGENTIC_RAG_PLANNER_ENABLED or not self._should_use_model_planner(query):
            return query, "rule"
        prompt = (
            "你是企业知识库检索规划器，只能优化检索表达，不回答用户问题，不添加事实。\n"
            '输出 JSON：{"search_query": "不超过300字的检索问题"}。\n'
            f"原始问题：{query}\n"
            + (f"上一轮判定缺少的信息：{missing_hint}\n" if missing_hint else "")
            + (
                "上一轮证据不足，请将问题改写为更利于定位制度、条款、日期、金额、责任人或例外条件的检索表达。"
                if refinement
                else "请保留原问题的业务实体、时间、数值和约束。"
            )
        )
        try:
            with observe_span("rag.plan", {"rag.planner": "llm"}):
                raw = await llm_service.generate(prompt, temperature=0.0, action="agentic_rag_plan", user_id=user_id)
            payload = llm_service.parse_json_object(raw)
            search_query = str(payload.get("search_query") or "").strip()
            if search_query and len(search_query) <= 300:
                return search_query, "llm"
        except Exception:
            pass
        return query, "rule_fallback"

    @staticmethod
    def _rule_refine(query: str, missing_hint: str = "") -> str:
        suffix = f" {missing_hint}" if missing_hint else " 关键条款 条件 例外 日期 金额 责任人"
        return f"{query}{suffix}"[:300]

    def _build_workflow(self):
        graph = StateGraph(AgenticRAGState)
        graph.add_node("plan", self._workflow_plan)
        graph.add_node("decompose", self._workflow_decompose)
        graph.add_node("sub_retrieve", self._workflow_sub_retrieve)
        graph.add_node("merge_evidence", self._workflow_merge_evidence)
        graph.add_node("retrieve", self._workflow_retrieve)
        graph.add_node("assess_evidence", self._workflow_assess_evidence)
        graph.add_node("refine", self._workflow_refine)
        graph.add_node("generate", self._workflow_generate)
        graph.add_node("verify_faithfulness", self._workflow_verify_faithfulness)
        graph.add_edge(GRAPH_START, "plan")
        graph.add_conditional_edges(
            "plan",
            self._route_after_plan,
            {"decompose": "decompose", "retrieve": "retrieve"},
        )
        # 分解成功 -> 每个子问题一个 Send 分支；拆不动 -> 退回单跳检索。
        graph.add_conditional_edges("decompose", self._route_after_decompose, ["sub_retrieve", "retrieve"])
        graph.add_edge("sub_retrieve", "merge_evidence")
        graph.add_edge("merge_evidence", "assess_evidence")
        graph.add_edge("retrieve", "assess_evidence")
        graph.add_conditional_edges(
            "assess_evidence",
            self._route_after_assessment,
            {"refine": "refine", "generate": "generate"},
        )
        graph.add_edge("refine", "retrieve")
        graph.add_edge("generate", "verify_faithfulness")
        graph.add_edge("verify_faithfulness", GRAPH_END)
        # checkpointer 让每次问答的图状态按 thread_id 落盘，可回放/断点续跑；
        # 安装 langgraph-checkpoint-sqlite 后自动升级为跨进程重启持久化。
        return graph.compile(checkpointer=build_checkpointer())

    async def _workflow_plan(self, state: dict[str, Any]) -> dict[str, Any]:
        query, mode = await self._plan_query(state["question"], user_id=state.get("user_id"))
        state["active_query"] = query
        state["trace"].append({"node": "plan", "mode": mode, "query_changed": query != state["question"]})
        return state

    def _route_after_plan(self, state: dict[str, Any]) -> str:
        if self.settings.AGENTIC_RAG_MULTI_HOP_ENABLED and self._looks_multi_hop(state["question"]):
            return "decompose"
        return "retrieve"

    async def _workflow_decompose(self, state: dict[str, Any]) -> dict[str, Any]:
        outcome = await decompose_question(
            state["question"],
            max_sub_questions=self.settings.AGENTIC_RAG_MAX_SUB_QUESTIONS,
            user_id=state.get("user_id"),
        )
        state["sub_questions"] = outcome.get("sub_questions") or []
        state["trace"].append(
            {
                "node": "decompose",
                "available": bool(outcome.get("available")),
                "sub_question_count": len(state["sub_questions"]),
                "sub_questions": state["sub_questions"],
            }
        )
        return state

    def _route_after_decompose(self, state: dict[str, Any]) -> Any:
        """一个子问题一个 ``Send`` 分支：分支是图任务，并发上限与 checkpoint 都由图管。

        分支拿到的是 ``Send`` 的载荷而不是整份 state，因此权限范围与检索参数必须随载荷一起
        带过去——这也让「分支不能绕过范围过滤」成为可断言的性质。
        """
        sub_questions = state.get("sub_questions") or []
        if not sub_questions:
            return "retrieve"
        scope = {key: state.get(key) for key in self._SCOPE_KEYS}
        return [
            Send(
                "sub_retrieve",
                {**scope, "runtime_config": state["runtime_config"], "index": index, "sub_question": sub_question},
            )
            for index, sub_question in enumerate(sub_questions)
        ]

    async def _workflow_sub_retrieve(self, branch: dict[str, Any]) -> dict[str, Any]:
        """一个子问题的检索分支：只写自己那个 ``sub_results`` 槽位。

        分支必须返回增量而不是整份 state——并发任务同时改写全部通道会直接
        ``InvalidUpdateError``，而按键合并的 reducer 只关心自己的槽位。
        """
        started = time.time()
        chunks = await rag_service.search_async(branch["sub_question"], **self._search_kwargs(branch))
        # 检索链路 trace 存在 ContextVar 里，而分支各自跑在独立 task（context 是副本），
        # 出了分支就取不到了，所以在分支内就地取走。
        retrieval_trace = get_last_trace()
        confidence = rag_service._estimate_confidence(branch["sub_question"], chunks) if chunks else 0.0
        return {
            "sub_results": {
                str(branch["index"]): {
                    "index": branch["index"],
                    "sub_question": branch["sub_question"],
                    "chunks": chunks,
                    "confidence": round(confidence, 4),
                    "duration_ms": int((time.time() - started) * 1000),
                    "retrieval_trace": retrieval_trace,
                }
            }
        }

    async def _workflow_merge_evidence(self, state: dict[str, Any]) -> dict[str, Any]:
        """按**子问题顺序**合并各分支证据：去重保序，再截到一次检索的上下文预算内。

        用计划顺序而不是完成顺序，同一次问答才可复现；并发分支的耗时取最大值而不是求和，
        否则观测到的检索延迟会被重复计算。
        """
        results = sorted((state.get("sub_results") or {}).values(), key=lambda item: item["index"])
        merged: list[Any] = []
        seen: set[str] = set()
        for item in results:
            for chunk in item["chunks"]:
                key = self._chunk_key(chunk)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(chunk)
        runtime_config = state["runtime_config"]
        merged = merged[: max(int(runtime_config["context_max_chunks"]), int(runtime_config["top_k"]))]
        # 合并集的置信度取**各分支的最小值**，而不是拿原问题去评合并集：
        # _estimate_confidence 的关键词/距离项只看前 3 个片段，而合并集的前 3 个片段属于第一个
        # 子问题，对整个问题的覆盖必然偏低——那会让刚扇出的证据立刻被判不足、再退回单跳。
        # 取最小值而非均值：某个子问题没查到东西时仍然要补检索。
        branch_confidences = [float(item["confidence"]) for item in results]
        confidence = min(branch_confidences) if branch_confidences and merged else 0.0
        state["round"] += 1
        state["retrieval_duration_ms"] += max((item["duration_ms"] for item in results), default=0)
        state["latest_chunks"] = merged
        state["latest_confidence"] = confidence
        # 保留首个分支 trace 的结构（rerank_status 等降级信号在里面），但候选数改成合并后的口径。
        base_trace = next((item["retrieval_trace"] for item in results if item.get("retrieval_trace")), None)
        retrieval_trace = {**(base_trace or {"trace_version": 1}), "retrieval": {}}
        retrieval_trace["retrieval"] = {
            **((base_trace or {}).get("retrieval") or {}),
            "reranked_candidates": len(merged),
            "sub_query_count": len(results),
        }
        state["latest_retrieval_trace"] = retrieval_trace
        if confidence >= state.get("best_confidence", -1.0):
            state["best_chunks"] = merged
            state["best_confidence"] = confidence
        self._record_round_evidence(state, merged, confidence)
        state["trace"].append(
            {
                "node": "merge_evidence",
                "round": state["round"],
                "sub_questions": [item["sub_question"] for item in results],
                "sub_hit_counts": [len(item["chunks"]) for item in results],
                "branch_confidences": [round(value, 4) for value in branch_confidences],
                "hit_count": len(merged),
                "confidence": round(confidence, 4),
            }
        )
        return state

    @staticmethod
    def _chunk_key(chunk: dict[str, Any]) -> str:
        """跨子问题去重：优先用向量库 id，缺失时退回 (文档, 分块序号)。"""
        metadata = chunk.get("metadata") or {}
        return str(
            chunk.get("id")
            or metadata.get("embedding_id")
            or f"{metadata.get('document_id')}:{metadata.get('chunk_index')}"
        )

    @staticmethod
    def _record_round_evidence(state: dict[str, Any], chunks: list[Any], confidence: float) -> None:
        state.setdefault("round_evidence", []).append(
            {"round": state["round"], "confidence": float(confidence), "chunks": list(chunks)}
        )

    def _accumulate_evidence(self, state: dict[str, Any]) -> tuple[list[Any], list[int]]:
        """把各轮证据并起来交给生成，而不是只交置信度最高的那一轮。

        判分器说「缺 X」、下一轮就照着 X 去查——若生成只看其中一轮，另一半证据等于白查：
        补检索会因此在覆盖率上完全不体现（离线消融里这一臂原本恒为 0 提升）。

        按轮置信度降序拼接（同分保持轮次先后），所以最好的一轮仍排在 prompt 最前面，其余
        轮次只是追加；再按 context 上限截断，避免多轮把上下文撑爆。
        """
        rounds = sorted(
            state.get("round_evidence") or [],
            key=lambda item: -float(item["confidence"]),
        )
        accumulated: list[Any] = []
        used: list[int] = []
        seen: set[str] = set()
        for item in rounds:
            added = False
            for chunk in item["chunks"]:
                key = self._chunk_key(chunk)
                if key in seen:
                    continue
                seen.add(key)
                accumulated.append(chunk)
                added = True
            if added:
                used.append(int(item["round"]))
        runtime_config = state["runtime_config"]
        limit = max(int(runtime_config["context_max_chunks"]), int(runtime_config["top_k"]))
        return accumulated[:limit], sorted(used)

    @staticmethod
    def _search_kwargs(scope: dict[str, Any]) -> dict[str, Any]:
        """检索参数与权限范围：单跳节点与子问题分支共用一份，分支因此无法绕过范围过滤。"""
        runtime_config = scope["runtime_config"]
        return {
            "document_id": scope.get("document_id"),
            "top_k": runtime_config["top_k"],
            "user_id": scope.get("user_id"),
            "min_recall_candidates": runtime_config["min_recall_candidates"],
            "recall_multiplier": runtime_config["recall_multiplier"],
            "query_variant_limit": runtime_config["query_variant_limit"],
            "knowledge_base_id": scope.get("knowledge_base_id"),
            "document_status": scope.get("document_status"),
            "authorized_document_ids": scope.get("authorized_document_ids"),
        }

    async def _workflow_retrieve(self, state: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        search_query = state["active_query"]
        # 会话记忆：追问消歧——用上一轮用户问题补全当前检索表达式
        history = state.get("conversation_history") or []
        last_user_question = next(
            (m.get("content") for m in reversed(history) if m.get("role") == "user"),
            None,
        )
        if last_user_question and last_user_question != state["question"]:
            search_query = f"{last_user_question} {search_query}"
        chunks = await rag_service.search_async(search_query, **self._search_kwargs(state))
        duration_ms = int((time.time() - started) * 1000)
        confidence = rag_service._estimate_confidence(state["question"], chunks) if chunks else 0.0
        state["round"] += 1
        state["retrieval_duration_ms"] += duration_ms
        state["latest_chunks"] = chunks
        retrieval_trace = get_last_trace() or {
            "trace_version": 1,
            "retrieval": {"reranked_candidates": len(chunks)},
        }
        state["latest_retrieval_trace"] = retrieval_trace
        state["latest_confidence"] = confidence
        if confidence >= state.get("best_confidence", -1.0):
            state["best_chunks"] = chunks
            state["best_confidence"] = confidence
        self._record_round_evidence(state, chunks, confidence)
        state["trace"].append(
            {
                "node": "retrieve",
                "round": state["round"],
                "hit_count": len(chunks),
                "confidence": round(confidence, 4),
                "duration_ms": duration_ms,
                "retrieval_trace": retrieval_trace.get("retrieval", {}),
            }
        )
        return state

    async def _workflow_assess_evidence(self, state: dict[str, Any]) -> dict[str, Any]:
        threshold = max(float(state["runtime_config"]["confidence_threshold"]), 0.45)
        evidence_ready = bool(state["latest_chunks"]) and state["latest_confidence"] >= threshold
        heuristic_ready = evidence_ready
        judgement: dict[str, Any] | None = None
        if self.settings.AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED and state["latest_chunks"]:
            judgement = await judge_evidence(state["question"], state["latest_chunks"], user_id=state.get("user_id"))
            state["evidence_judgement"] = judgement
            if judgement.get("available"):
                if judgement["sufficient"] is False and evidence_ready:
                    # 只有还剩轮次时才允许否决——最后一轮否决等于把可答的问题变成拒答，
                    # 而判断器的职责是「要不要再查一轮」，不是替生成节点决定拒答。
                    evidence_ready = state["round"] >= state["max_rounds"]
                elif judgement["sufficient"] and not evidence_ready:
                    # 反向救回：相似度分数低但片段确实答得上（同义表述、表格化条款）。
                    evidence_ready = True
        state["evidence_ready"] = evidence_ready
        step: dict[str, Any] = {
            "node": "assess_evidence",
            "round": state["round"],
            "decision": "generate" if evidence_ready else "refine_or_refuse",
            "threshold": threshold,
        }
        if judgement is not None:
            step["judge"] = {
                "available": bool(judgement.get("available")),
                "sufficient": judgement.get("sufficient"),
                "score": judgement.get("score"),
                "missing": judgement.get("missing"),
                "overrode_heuristic": evidence_ready != heuristic_ready,
            }
        state["trace"].append(step)
        return state

    def _route_after_assessment(self, state: dict[str, Any]) -> str:
        if not state["evidence_ready"] and state["round"] < state["max_rounds"]:
            return "refine"
        return "generate"

    async def _workflow_refine(self, state: dict[str, Any]) -> dict[str, Any]:
        # 判断器说缺什么，就把「缺什么」写进下一轮检索表达式；否则只是把同一个查询再跑一遍。
        missing_hint = str((state.get("evidence_judgement") or {}).get("missing") or "").strip()
        refined_query, mode = await self._plan_query(
            state["question"],
            user_id=state.get("user_id"),
            refinement=True,
            missing_hint=missing_hint,
        )
        if refined_query == state["active_query"]:
            refined_query = self._rule_refine(state["question"], missing_hint)
            mode = "rule_refinement"
        state["active_query"] = refined_query
        state["trace"].append(
            {"node": "refine", "round": state["round"], "mode": mode, "missing_hint": missing_hint or None}
        )
        return state

    async def _workflow_generate(self, state: dict[str, Any]) -> dict[str, Any]:
        # 交给生成的是**各轮并集**（去重后按 context 上限截断），不是单独某一轮：
        # 后续的忠实性校验也照这一份核对，两处必须是同一个证据集合。
        evidence, evidence_rounds = self._accumulate_evidence(state)
        state["best_chunks"] = evidence
        with observe_span("rag.agentic", {"rag.rounds": state["round"]}):
            result = await rag_service.answer_from_chunks_async(
                state["question"],
                chunks=evidence,
                document_id=state.get("document_id"),
                user_id=state.get("user_id"),
                runtime_config=state["runtime_config"],
                started=state["started"],
                retrieval_duration_ms=state["retrieval_duration_ms"],
                log_query=state["question"],
                knowledge_base_id=state.get("knowledge_base_id"),
                document_status=state.get("document_status"),
                authorized_document_ids=state.get("authorized_document_ids"),
                conversation_history=state.get("conversation_history"),
                retrieval_trace=state.get("latest_retrieval_trace"),
            )
        trace = {
            "enabled": True,
            "trace_version": 1,
            "workflow_engine": workflow_engine_name(),
            "retrieval_rounds": state["round"],
            "final_evidence_confidence": round(max(state.get("best_confidence", 0.0), 0.0), 4),
            "evidence": {"chunk_count": len(evidence), "from_rounds": evidence_rounds},
            "steps": state["trace"],
            "generation": {
                "status": "success" if result.get("can_answer") else "degraded",
                "duration_ms": result.get("observability", {}).get("generation_duration_ms", 0),
                "degradation_reason": result.get("refusal_reason"),
            },
        }
        result["agentic_rag"] = trace
        result.setdefault("observability", {})["trace_version"] = 1
        result.setdefault("observability", {})["degradation_reason"] = result.get("refusal_reason")
        result.setdefault("observability", {})["agentic_retrieval_rounds"] = state["round"]
        llm_observability_service.log_event(
            module_name="document",
            action="agentic_rag",
            model_name=self.settings.LLM_MODEL,
            status="success" if result.get("can_answer") else "refused",
            duration_ms=result.get("latency_ms"),
            user_id=state.get("user_id"),
            request_excerpt={"document_id": state.get("document_id"), "max_rounds": state["max_rounds"]},
            response_excerpt={"rounds": state["round"], "confidence": trace["final_evidence_confidence"]},
        )
        state["result"] = result
        return state

    async def _workflow_verify_faithfulness(self, state: dict[str, Any]) -> dict[str, Any]:
        """生成后校验：答案里有片段查不到的断言就降级为拒答。

        只对「已经敢答」的结果做检查——拒答结果没有需要核对的断言，再花一次往返没有意义。
        """
        result = state.get("result") or {}
        agentic = result.setdefault("agentic_rag", {})
        checked: dict[str, Any] | None = None
        if self.settings.AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED and result.get("can_answer"):
            checked = await check_faithfulness(
                state["question"],
                str(result.get("answer") or ""),
                state["best_chunks"],
                user_id=state.get("user_id"),
            )
            state["faithfulness"] = checked
            if checked.get("available") and checked["faithful"] is False:
                self._degrade_unfaithful(result, checked)
                generation = agentic.setdefault("generation", {})
                generation["status"] = "degraded"
                generation["degradation_reason"] = _UNFAITHFUL_REFUSAL
            state["trace"].append(
                {
                    "node": "verify_faithfulness",
                    "available": bool(checked.get("available")),
                    "faithful": checked.get("faithful"),
                    "unsupported_count": len(checked.get("unsupported") or []),
                    "reason": checked.get("reason") or None,
                }
            )
        agentic["faithfulness"] = checked
        # generate 里的 steps 是 state["trace"] 的别名，跨 superstep 不保证还是同一个对象
        # （checkpoint 往返会拷贝），所以这里显式重写一份，而不是指望原地追加可见。
        agentic["steps"] = list(state["trace"])
        state["result"] = result
        return state

    @staticmethod
    def _degrade_unfaithful(result: dict[str, Any], checked: dict[str, Any]) -> None:
        """降级成拒答，而不是改写答案。

        让判断器改写答案等于让它生成事实，那是它明确不做的事。降级口径与既有拒答一致
        （``can_answer=False`` + ``refusal_reason``），前端与观测都不必为这一种情况加分支。
        """
        result["answer"] = _UNFAITHFUL_ANSWER
        result["can_answer"] = False
        result["confidence"] = min(float(result.get("confidence") or 0.0), 0.3)
        result["refusal_reason"] = _UNFAITHFUL_REFUSAL
        observability = result.setdefault("observability", {})
        observability["result_status"] = "refused"
        observability["refusal_reason"] = _UNFAITHFUL_REFUSAL
        observability["degradation_reason"] = _UNFAITHFUL_REFUSAL
        observability["unsupported_claim_count"] = len(checked.get("unsupported") or [])

    async def answer_async(
        self,
        question: str,
        *,
        document_id: int | None = None,
        user_id: int | None = None,
        knowledge_base_id: int | None = None,
        document_status: str | None = None,
        authorized_document_ids: list[int] | None = None,
        conversation_history: list[dict] | None = None,
        **runtime_overrides: Any,
    ) -> dict[str, Any]:
        runtime_config = rag_service.get_runtime_config(**runtime_overrides)
        if not self.settings.AGENTIC_RAG_ENABLED:
            result = await rag_service.answer_async(
                question,
                document_id=document_id,
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                document_status=document_status,
                authorized_document_ids=authorized_document_ids,
                conversation_history=conversation_history,
                **runtime_overrides,
            )
            result["agentic_rag"] = {"enabled": False, "reason": "feature_disabled"}
            return result
        state = {
            "question": question,
            "document_id": document_id,
            "user_id": user_id,
            "knowledge_base_id": knowledge_base_id,
            "document_status": document_status,
            "authorized_document_ids": authorized_document_ids,
            "conversation_history": conversation_history,
            "runtime_config": runtime_config,
            "max_rounds": self.settings.AGENTIC_RAG_MAX_RETRIEVAL_ROUNDS,
            "round": 0,
            "retrieval_duration_ms": 0,
            "latest_chunks": [],
            "latest_confidence": 0.0,
            "best_chunks": [],
            "best_confidence": -1.0,
            "round_evidence": [],
            "trace": [],
            "started": time.time(),
            "result": None,
            "latest_retrieval_trace": None,
            "sub_questions": [],
            "sub_results": {},
            "evidence_judgement": None,
            "faithfulness": None,
        }
        # thread_id 是 checkpointer 的持久化主键：一次问答一个线程，可按此回放该次检索的每一步状态。
        config = {"configurable": {"thread_id": f"rag-{user_id or 'anon'}-{uuid.uuid4().hex}"}}
        final_state = await self._workflow.ainvoke(state, config)
        return final_state["result"]

    def answer(self, question: str, **kwargs: Any) -> dict[str, Any]:
        return run_async(self.answer_async(question, **kwargs))


agentic_rag_service = AgenticRAGService()
