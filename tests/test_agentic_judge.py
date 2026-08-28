"""``agentic_judge`` 的三个判断器：测规范化与 fail-open，不测 prompt 文案。

判断器的取舍是「说不清就别拦」：LLM 不可用或输出不可解析时必须返回 ``available=False``，
让图退回启发式规则，而不是让问答失败。所以三个函数各有一条 fail-open 用例。
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.services.rag import agentic_judge


def _generate(*returns: str) -> AsyncMock:
    """按调用顺序返回预设文本；只给一个值时每次都返回它。"""
    if len(returns) == 1:
        return AsyncMock(return_value=returns[0])
    return AsyncMock(side_effect=list(returns))


class DecomposeQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_sub_questions_and_tags_the_action(self):
        raw = '{"sub_questions": ["试用期最长多久", "经济补偿怎么算"]}'
        with patch.object(agentic_judge.llm_service, "generate", _generate(raw)) as generate:
            outcome = await agentic_judge.decompose_question(
                "试用期最长多久，以及经济补偿怎么算",
                max_sub_questions=3,
                user_id=7,
            )

        self.assertTrue(outcome["available"])
        self.assertEqual(outcome["sub_questions"], ["试用期最长多久", "经济补偿怎么算"])
        self.assertEqual(generate.await_args.kwargs["action"], "agentic_rag_decompose")
        self.assertEqual(generate.await_args.kwargs["user_id"], 7)

    async def test_single_sub_question_is_not_multi_hop(self):
        """拆出一条等于把原问题改写了一遍，那是 plan 节点的事，扇出一路白跑。"""
        with patch.object(agentic_judge.llm_service, "generate", _generate('{"sub_questions": ["经济补偿怎么算"]}')):
            outcome = await agentic_judge.decompose_question("经济补偿怎么算", max_sub_questions=3)

        self.assertTrue(outcome["available"])
        self.assertEqual(outcome["sub_questions"], [])

    async def test_dedupes_drops_echo_of_question_and_caps(self):
        question = "试用期最长多久，以及经济补偿怎么算"
        raw = (
            '{"sub_questions": ["试用期最长多久", "试用期最长多久", '
            f'"{question}", "经济补偿怎么算", "竞业限制期限", "加班费基数"]}}'
        )
        with patch.object(agentic_judge.llm_service, "generate", _generate(raw)):
            outcome = await agentic_judge.decompose_question(question, max_sub_questions=3)

        self.assertEqual(outcome["sub_questions"], ["试用期最长多久", "经济补偿怎么算", "竞业限制期限"])

    async def test_fails_open_when_the_model_is_unavailable(self):
        with patch.object(agentic_judge.llm_service, "generate", AsyncMock(side_effect=RuntimeError("boom"))):
            outcome = await agentic_judge.decompose_question("A，以及 B", max_sub_questions=3)

        self.assertFalse(outcome["available"])
        self.assertEqual(outcome["reason"], "decompose_unavailable")
        self.assertEqual(outcome["sub_questions"], [])

    async def test_fails_open_when_the_payload_has_no_list(self):
        with patch.object(agentic_judge.llm_service, "generate", _generate('{"sub_questions": "试用期"}')):
            outcome = await agentic_judge.decompose_question("A，以及 B", max_sub_questions=3)

        self.assertFalse(outcome["available"])
        self.assertEqual(outcome["sub_questions"], [])


class JudgeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.chunks = [{"content": f"第 {index} 段制度说明。"} for index in range(1, 9)]

    async def test_clamps_score_and_keeps_the_missing_hint(self):
        """``missing`` 是这一步真正的产出：它会被拼进下一轮检索表达式。"""
        raw = '{"sufficient": false, "score": 1.7, "missing": "缺少提交期限"}'
        with patch.object(agentic_judge.llm_service, "generate", _generate(raw)):
            outcome = await agentic_judge.judge_evidence("多久内提交", self.chunks)

        self.assertTrue(outcome["available"])
        self.assertFalse(outcome["sufficient"])
        self.assertEqual(outcome["score"], 1.0)
        self.assertEqual(outcome["missing"], "缺少提交期限")

    async def test_tolerates_a_missing_score(self):
        with patch.object(agentic_judge.llm_service, "generate", _generate('{"sufficient": true}')):
            outcome = await agentic_judge.judge_evidence("多久内提交", self.chunks)

        self.assertTrue(outcome["sufficient"])
        self.assertIsNone(outcome["score"])
        self.assertEqual(outcome["missing"], "")

    async def test_prompt_numbers_and_caps_the_evidence(self):
        """片段编号让判断器能指名道姓地说哪一段支持了什么；上限保证 prompt 不随召回膨胀。"""
        with patch.object(agentic_judge.llm_service, "generate", _generate('{"sufficient": true}')) as generate:
            await agentic_judge.judge_evidence("多久内提交", self.chunks)

        prompt = generate.await_args.args[0]
        self.assertIn("[片段 1]", prompt)
        self.assertIn("[片段 6]", prompt)
        self.assertNotIn("[片段 7]", prompt)

    async def test_fails_open_on_unparsable_output(self):
        with patch.object(agentic_judge.llm_service, "generate", _generate("这不是 JSON")):
            outcome = await agentic_judge.judge_evidence("多久内提交", self.chunks)

        self.assertFalse(outcome["available"])
        self.assertIsNone(outcome["sufficient"])
        self.assertEqual(outcome["reason"], "judge_unavailable")


class CheckFaithfulnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_and_caps_unsupported_claims(self):
        raw = (
            '{"faithful": false, "unsupported": ["30 日内提交", "补偿 5000 元", '
            '"依据第三条", "第四条也没有"], "reason": "片段无此期限"}'
        )
        with patch.object(agentic_judge.llm_service, "generate", _generate(raw)):
            outcome = await agentic_judge.check_faithfulness("多久内提交", "应在 30 日内提交。", [{"content": "制度"}])

        self.assertTrue(outcome["available"])
        self.assertFalse(outcome["faithful"])
        self.assertEqual(outcome["unsupported"], ["30 日内提交", "补偿 5000 元", "依据第三条"])
        self.assertEqual(outcome["reason"], "片段无此期限")

    async def test_clips_a_long_answer_out_of_the_prompt(self):
        answer = "甲" * 4000
        with patch.object(agentic_judge.llm_service, "generate", _generate('{"faithful": true}')) as generate:
            outcome = await agentic_judge.check_faithfulness("问题", answer, [{"content": "制度"}])

        prompt = generate.await_args.args[0]
        self.assertLess(prompt.count("甲"), len(answer))
        self.assertTrue(outcome["faithful"])
        self.assertEqual(outcome["unsupported"], [])

    async def test_fails_open_when_the_model_is_unavailable(self):
        with patch.object(agentic_judge.llm_service, "generate", AsyncMock(side_effect=TimeoutError())):
            outcome = await agentic_judge.check_faithfulness("问题", "答案", [{"content": "制度"}])

        self.assertFalse(outcome["available"])
        self.assertIsNone(outcome["faithful"])
        self.assertEqual(outcome["reason"], "faithfulness_unavailable")


if __name__ == "__main__":
    unittest.main()
