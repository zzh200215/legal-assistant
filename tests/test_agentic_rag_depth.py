"""Agentic-RAG 深度能力：多跳扇出、证据判分否决/救回、生成后忠实性降级。

三项能力都默认关闭，所以每条用例显式打开自己那一项——顺带守住「不开就不多花一次 LLM
往返」这条不变量。检索 / 置信度 / 生成三处都是桩：这些用例只关心图往哪走、状态怎么合。
"""

import copy
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

from app.core.config.rag import RAGSettings
from app.services.rag.agentic_rag_service import AgenticRAGService

_MODULE = "app.services.rag.agentic_rag_service."


@contextmanager
def _settings(service, **overrides):
    """临时改写 settings：``get_settings()`` 是缓存单例，测完必须还原。"""
    previous = {key: getattr(service.settings, key) for key in overrides}
    for key, value in overrides.items():
        setattr(service.settings, key, value)
    try:
        yield
    finally:
        for key, value in previous.items():
            setattr(service.settings, key, value)


class _Stubs:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.search = None
        self.answer = None

    @property
    def queries(self) -> list[str]:
        return [query for query, _ in self.calls]


@contextmanager
def _stub_rag(*, results, confidence, answer):
    """桩住检索 / 置信度 / 生成。

    ``results`` 是列表时每次检索都返回它；是 dict 时按「查询里包含的关键字」取——
    子问题分支是并发的，按查询取结果才不依赖分支完成顺序。
    """
    stubs = _Stubs()

    async def fake_search(query, **kwargs):
        stubs.calls.append((query, kwargs))
        if isinstance(results, dict):
            return next((value for key, value in results.items() if key in query), [])
        return results

    confidence_kwargs = (
        {"return_value": confidence} if isinstance(confidence, int | float) else {"side_effect": list(confidence)}
    )
    with ExitStack() as stack:
        stubs.search = stack.enter_context(
            patch(f"{_MODULE}rag_service.search_async", new=AsyncMock(side_effect=fake_search))
        )
        stack.enter_context(patch(f"{_MODULE}rag_service._estimate_confidence", **confidence_kwargs))
        stubs.answer = stack.enter_context(
            patch(
                f"{_MODULE}rag_service.answer_from_chunks_async",
                new=AsyncMock(side_effect=lambda *a, **k: copy.deepcopy(answer)),
            )
        )
        stack.enter_context(patch(f"{_MODULE}llm_observability_service.log_event"))
        yield stubs


def _chunk(chunk_id: str, content: str, chunk_index: int) -> dict:
    return {
        "id": chunk_id,
        "content": content,
        "metadata": {"document_id": 1, "chunk_id": chunk_index + 1, "chunk_index": chunk_index},
        "distance": 0.1,
    }


def _answer(text="试用期最长 6 个月。[片段 1]", *, can_answer=True, refusal_reason=None, confidence=0.8) -> dict:
    return {
        "answer": text,
        "citations": [],
        "hit_chunks": [],
        "context_chunks": [],
        "confidence": confidence,
        "can_answer": can_answer,
        "refusal_reason": refusal_reason,
        "latency_ms": 12,
        "observability": {"result_status": "answered" if can_answer else "refused"},
    }


class _DepthTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = AgenticRAGService()
        self.first = _chunk("c1", "试用期最长不超过 6 个月。", 0)
        self.shared = _chunk("c2", "劳动合同解除的一般规定。", 1)
        self.second = _chunk("c3", "经济补偿按工作年限每满一年支付一个月工资。", 2)

    @staticmethod
    def _nodes(result: dict) -> list[str]:
        return [step["node"] for step in result["agentic_rag"]["steps"]]

    @staticmethod
    def _step(result: dict, node: str, *, last=False) -> dict:
        steps = [step for step in result["agentic_rag"]["steps"] if step["node"] == node]
        return steps[-1] if last else steps[0]


class MultiHopFanOutTests(_DepthTestCase):
    question = "试用期最长多久，以及经济补偿怎么算"

    def _decompose(self, sub_questions, available=True):
        return patch(
            f"{_MODULE}decompose_question",
            new=AsyncMock(return_value={"available": available, "sub_questions": sub_questions}),
        )

    async def test_each_sub_question_becomes_its_own_retrieval_branch(self):
        """两个子问题 -> 两次检索；证据按**计划顺序**去重合并，与分支完成顺序无关。"""
        results = {"试用期": [self.first, self.shared], "经济补偿": [self.shared, self.second]}
        with _settings(
            self.service,
            AGENTIC_RAG_MULTI_HOP_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), self._decompose(["试用期最长多久", "经济补偿怎么算"]), _stub_rag(
            results=results, confidence=0.8, answer=_answer()
        ) as stubs:
            result = await self.service.answer_async(self.question, document_id=1, user_id=7)

        self.assertEqual(sorted(stubs.queries), ["经济补偿怎么算", "试用期最长多久"])
        self.assertEqual(
            stubs.answer.await_args.kwargs["chunks"],
            [self.first, self.shared, self.second],
        )
        self.assertEqual(self._nodes(result)[:3], ["plan", "decompose", "merge_evidence"])
        self.assertNotIn("retrieve", self._nodes(result))
        # 并发的两个分支算一轮检索，不是两轮。
        self.assertEqual(result["agentic_rag"]["retrieval_rounds"], 1)
        merge = self._step(result, "merge_evidence")
        self.assertEqual(merge["sub_questions"], ["试用期最长多久", "经济补偿怎么算"])
        self.assertEqual(merge["sub_hit_counts"], [2, 2])
        self.assertEqual(merge["hit_count"], 3)

    async def test_branches_cannot_widen_the_permission_scope(self):
        """分支只拿到 ``Send`` 载荷，范围过滤必须逐个带过去，否则分支等于越权检索。"""
        with _settings(
            self.service,
            AGENTIC_RAG_MULTI_HOP_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), self._decompose(["试用期最长多久", "经济补偿怎么算"]), _stub_rag(
            results=[self.first], confidence=0.8, answer=_answer()
        ) as stubs:
            await self.service.answer_async(
                self.question,
                document_id=1,
                user_id=7,
                knowledge_base_id=3,
                document_status="indexed",
                authorized_document_ids=[1, 2],
            )

        self.assertEqual(len(stubs.calls), 2)
        for _, kwargs in stubs.calls:
            self.assertEqual(kwargs["document_id"], 1)
            self.assertEqual(kwargs["user_id"], 7)
            self.assertEqual(kwargs["knowledge_base_id"], 3)
            self.assertEqual(kwargs["document_status"], "indexed")
            self.assertEqual(kwargs["authorized_document_ids"], [1, 2])

    async def test_falls_back_to_single_hop_when_decomposition_is_unavailable(self):
        """判断器不可用是常态而非异常：拆不出来就按单跳走，不能让问答失败。"""
        with _settings(
            self.service,
            AGENTIC_RAG_MULTI_HOP_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), self._decompose([], available=False), _stub_rag(
            results=[self.first], confidence=0.8, answer=_answer()
        ) as stubs:
            result = await self.service.answer_async(self.question, document_id=1, user_id=7)

        self.assertEqual(stubs.queries, [self.question])
        self.assertEqual(self._nodes(result), ["plan", "decompose", "retrieve", "assess_evidence"])
        self.assertEqual(self._step(result, "decompose")["sub_question_count"], 0)

    async def test_single_intent_question_never_pays_for_decomposition(self):
        """前置门槛拦在 LLM 之前：只问一件事时连分解那一次往返都不发。"""
        with _settings(
            self.service,
            AGENTIC_RAG_MULTI_HOP_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), self._decompose(["A", "B"]) as decompose, _stub_rag(
            results=[self.first], confidence=0.8, answer=_answer()
        ) as stubs:
            result = await self.service.answer_async("加班费怎么算", document_id=1, user_id=7)

        decompose.assert_not_awaited()
        self.assertEqual(len(stubs.calls), 1)
        self.assertNotIn("decompose", self._nodes(result))


class EvidenceJudgeTests(_DepthTestCase):
    question = "差旅报销多久内提交"

    @staticmethod
    def _judge(*outcomes):
        merged = [{"available": True, "sufficient": True, "score": 0.9, "missing": "", **item} for item in outcomes]
        return patch(f"{_MODULE}judge_evidence", new=AsyncMock(side_effect=merged))

    async def test_judge_can_force_one_more_round_and_says_what_is_missing(self):
        """判分的价值在 ``missing``：不把「还缺什么」写进下一轮，就只是重跑同一个查询。"""
        with _settings(
            self.service,
            AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), self._judge(
            {"sufficient": False, "score": 0.4, "missing": "缺少提交期限"},
            {"sufficient": True},
        ), _stub_rag(results=[self.first], confidence=0.8, answer=_answer()) as stubs:
            result = await self.service.answer_async(self.question, document_id=1, user_id=7)

        self.assertEqual(len(stubs.calls), 2)
        self.assertIn("缺少提交期限", stubs.queries[1])
        self.assertTrue(self._step(result, "assess_evidence")["judge"]["overrode_heuristic"])
        self.assertEqual(self._step(result, "refine")["missing_hint"], "缺少提交期限")
        self.assertEqual(result["agentic_rag"]["retrieval_rounds"], 2)

    async def test_judge_cannot_veto_on_the_last_round(self):
        """最后一轮再否决就等于把可答的问题变成拒答——那不是判分器该做的决定。"""
        with _settings(
            self.service,
            AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
            AGENTIC_RAG_MAX_RETRIEVAL_ROUNDS=2,
        ), self._judge(
            {"sufficient": False, "missing": "缺少期限"},
            {"sufficient": False, "missing": "缺少期限"},
        ), _stub_rag(results=[self.first], confidence=0.8, answer=_answer()) as stubs:
            result = await self.service.answer_async(self.question, document_id=1, user_id=7)

        self.assertEqual(len(stubs.calls), 2)
        stubs.answer.assert_awaited_once()
        self.assertTrue(result["can_answer"])
        last_assess = self._step(result, "assess_evidence", last=True)
        self.assertEqual(last_assess["decision"], "generate")
        self.assertFalse(last_assess["judge"]["overrode_heuristic"])

    async def test_judge_can_rescue_a_low_similarity_score(self):
        """分数低但片段确实答得上（同义表述、表格条款）时省掉一轮无用检索。"""
        with _settings(
            self.service,
            AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), self._judge({"sufficient": True, "score": 0.7}), _stub_rag(
            results=[self.first], confidence=0.2, answer=_answer()
        ) as stubs:
            result = await self.service.answer_async(self.question, document_id=1, user_id=7)

        self.assertEqual(len(stubs.calls), 1)
        self.assertNotIn("refine", self._nodes(result))
        self.assertTrue(self._step(result, "assess_evidence")["judge"]["overrode_heuristic"])

    async def test_unavailable_judge_leaves_the_heuristic_decision_alone(self):
        with _settings(
            self.service,
            AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), patch(
            f"{_MODULE}judge_evidence",
            new=AsyncMock(return_value={"available": False, "reason": "judge_unavailable", "sufficient": None}),
        ), _stub_rag(results=[self.first], confidence=0.8, answer=_answer()) as stubs:
            result = await self.service.answer_async(self.question, document_id=1, user_id=7)

        self.assertEqual(len(stubs.calls), 1)
        judge = self._step(result, "assess_evidence")["judge"]
        self.assertFalse(judge["available"])
        self.assertFalse(judge["overrode_heuristic"])


class EvidenceAccumulationTests(_DepthTestCase):
    """补检索的证据要**累积**：只把置信度最高的那一轮交给生成，等于把上一轮查到的丢掉。"""

    question = "差旅报销多久内提交"

    @staticmethod
    def _judge(*outcomes):
        merged = [{"available": True, "sufficient": True, "score": 0.9, "missing": "", **item} for item in outcomes]
        return patch(f"{_MODULE}judge_evidence", new=AsyncMock(side_effect=merged))

    async def _two_rounds(self, results):
        with _settings(
            self.service,
            AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), self._judge(
            {"sufficient": False, "score": 0.4, "missing": "缺少经济补偿标准"},
            {"sufficient": True},
        ), _stub_rag(results=results, confidence=[0.2, 0.8], answer=_answer()) as stubs:
            result = await self.service.answer_async(self.question, document_id=1, user_id=7)
        return result, stubs

    async def test_generation_sees_both_rounds_best_round_first(self):
        """两轮各查到一半：生成必须同时看到两半，且高分那一轮排在 prompt 前面。"""
        result, stubs = await self._two_rounds(
            {"缺少经济补偿标准": [self.second], self.question: [self.first]},
        )

        self.assertEqual(stubs.answer.await_args.kwargs["chunks"], [self.second, self.first])
        self.assertEqual(result["agentic_rag"]["evidence"], {"chunk_count": 2, "from_rounds": [1, 2]})

    async def test_chunks_seen_in_both_rounds_are_not_duplicated(self):
        result, stubs = await self._two_rounds(
            {"缺少经济补偿标准": [self.shared, self.second], self.question: [self.first, self.shared]},
        )

        self.assertEqual(stubs.answer.await_args.kwargs["chunks"], [self.shared, self.second, self.first])
        self.assertEqual(result["agentic_rag"]["evidence"]["chunk_count"], 3)


class FaithfulnessTests(_DepthTestCase):
    question = "差旅报销多久内提交"

    @staticmethod
    def _check(**outcome):
        payload = {"available": True, "faithful": True, "unsupported": [], "reason": "", **outcome}
        return patch(f"{_MODULE}check_faithfulness", new=AsyncMock(return_value=payload))

    async def _run(self, answer, check):
        with _settings(
            self.service,
            AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED=True,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), check as checker, _stub_rag(results=[self.first], confidence=0.8, answer=answer):
            return await self.service.answer_async(self.question, document_id=1, user_id=7), checker

    async def test_unsupported_claims_downgrade_the_answer_to_a_refusal(self):
        """降级而不是改写：让判断器改答案等于让它生成事实，那是它明确不做的事。"""
        result, _ = await self._run(
            _answer("应在 30 日内提交。[片段 1]"),
            self._check(faithful=False, unsupported=["30 日内提交"], reason="片段无此期限"),
        )

        self.assertFalse(result["can_answer"])
        self.assertEqual(result["refusal_reason"], "unfaithful_generation")
        self.assertNotIn("30 日", result["answer"])
        self.assertLessEqual(result["confidence"], 0.3)
        self.assertEqual(result["observability"]["result_status"], "refused")
        self.assertEqual(result["observability"]["degradation_reason"], "unfaithful_generation")
        self.assertEqual(result["observability"]["unsupported_claim_count"], 1)
        self.assertEqual(result["agentic_rag"]["generation"]["status"], "degraded")
        self.assertEqual(self._nodes(result)[-1], "verify_faithfulness")
        self.assertFalse(result["agentic_rag"]["faithfulness"]["faithful"])

    async def test_faithful_answer_is_left_untouched(self):
        result, _ = await self._run(_answer("试用期最长 6 个月。[片段 1]"), self._check())

        self.assertTrue(result["can_answer"])
        self.assertEqual(result["answer"], "试用期最长 6 个月。[片段 1]")
        self.assertIsNone(result["refusal_reason"])
        self.assertEqual(result["agentic_rag"]["generation"]["status"], "success")
        self.assertTrue(result["agentic_rag"]["faithfulness"]["faithful"])
        self.assertEqual(self._nodes(result)[-1], "verify_faithfulness")

    async def test_refusals_do_not_pay_for_the_check(self):
        """拒答里没有需要核对的断言，再花一次往返没有意义。"""
        result, checker = await self._run(
            _answer("根据当前文档内容，无法确认该问题。", can_answer=False, refusal_reason="low_confidence"),
            self._check(),
        )

        checker.assert_not_awaited()
        self.assertEqual(result["refusal_reason"], "low_confidence")
        self.assertIsNone(result["agentic_rag"]["faithfulness"])
        self.assertEqual(self._nodes(result)[-1], "assess_evidence")

    async def test_unavailable_check_leaves_the_answer_alone(self):
        result, _ = await self._run(
            _answer("试用期最长 6 个月。[片段 1]"),
            patch(
                f"{_MODULE}check_faithfulness",
                new=AsyncMock(return_value={"available": False, "reason": "faithfulness_unavailable", "faithful": None}),
            ),
        )

        self.assertTrue(result["can_answer"])
        self.assertEqual(result["agentic_rag"]["generation"]["status"], "success")
        self.assertFalse(self._step(result, "verify_faithfulness")["available"])


class DefaultsTests(unittest.TestCase):
    def test_the_three_extra_llm_round_trips_are_opt_in(self):
        """默认关闭是刻意的成本取舍，且有实测依据；改默认值请连带改文档。

        27 题法规多跳消融（eval/run_agentic_rag_eval.py --corpus-kind statutes）在生产
        默认 top_k=5 上的结论：判分补检索零收益、恒定多 2 次 LLM 往返；多跳分解本身有
        收益，但 _looks_multi_hop 只放行 3.7% 的自然提问，且 top_k=8 时反降 16.7pt。
        数字见 eval/results.md。
        """
        fields = RAGSettings.model_fields
        self.assertFalse(fields["AGENTIC_RAG_MULTI_HOP_ENABLED"].default)
        self.assertFalse(fields["AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED"].default)
        self.assertFalse(fields["AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED"].default)


class NoExtraCallsWhenDisabledTests(_DepthTestCase):
    async def test_disabled_features_add_no_llm_round_trips(self):
        with _settings(
            self.service,
            AGENTIC_RAG_MULTI_HOP_ENABLED=False,
            AGENTIC_RAG_EVIDENCE_JUDGE_ENABLED=False,
            AGENTIC_RAG_FAITHFULNESS_CHECK_ENABLED=False,
            AGENTIC_RAG_PLANNER_ENABLED=False,
        ), patch(f"{_MODULE}decompose_question", new=AsyncMock()) as decompose, patch(
            f"{_MODULE}judge_evidence", new=AsyncMock()
        ) as judge, patch(f"{_MODULE}check_faithfulness", new=AsyncMock()) as check, _stub_rag(
            results=[self.first], confidence=0.8, answer=_answer()
        ):
            result = await self.service.answer_async("试用期最长多久，以及经济补偿怎么算", document_id=1, user_id=7)

        decompose.assert_not_awaited()
        judge.assert_not_awaited()
        check.assert_not_awaited()
        self.assertEqual(self._nodes(result), ["plan", "retrieve", "assess_evidence"])


if __name__ == "__main__":
    unittest.main()
