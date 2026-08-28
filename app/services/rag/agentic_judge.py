"""Agentic-RAG 的三个模型判断：多跳分解、证据判分、生成后忠实性校验。

这三步共同点是「多一次 LLM 往返，换一次质量判断」，因此：

- 都由独立开关控制（见 :class:`app.core.config.rag.RAGSettings`），默认关闭；
- 都 fail-open：调用异常或 JSON 不可解析时返回 ``available=False``，图退回启发式规则，
  绝不因为判断器不可用而让问答失败；
- 判断结果只回答「够不够 / 站不站得住」，不生成事实内容，也不改写答案正文——改写与
  降级由图节点按既有的拒答口径处理，判断器不越权。

放在服务之外是为了让 prompt 与解析可以单独测试：图的路由测试不该被 prompt 文案绑住。
"""

from __future__ import annotations

from typing import Any

from app.services.llm.llm_service import llm_service

_SUB_QUESTION_MAX_CHARS = 120
_EVIDENCE_EXCERPT_CHARS = 300
_EVIDENCE_MAX_CHUNKS = 6
_ANSWER_MAX_CHARS = 1500
_UNSUPPORTED_MAX_ITEMS = 3


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _evidence_block(chunks: list[dict[str, Any]]) -> str:
    """把片段编号后拼进 prompt：编号让判断器可以指名道姓地说哪一段支持了什么。"""
    lines = []
    for index, chunk in enumerate(chunks[:_EVIDENCE_MAX_CHUNKS], start=1):
        lines.append(f"[片段 {index}]\n{_clip(chunk.get('content'), _EVIDENCE_EXCERPT_CHARS)}")
    return "\n\n".join(lines) or "无"


def _unavailable(reason: str, /, **extra: Any) -> dict[str, Any]:
    """判断器不可用时的统一返回。

    ``reason`` 是仅位置参数：各判断器的结果里本来就有自己的 ``reason`` 字段（不忠实的
    原因），若可作为关键字传入就会和这里的「不可用原因」撞名。
    """
    return {"available": False, "reason": reason, **extra}


async def decompose_question(
    question: str,
    *,
    max_sub_questions: int,
    user_id: int | None = None,
) -> dict[str, Any]:
    """把复杂问题拆成可独立检索的子问题；拆不动时返回空列表（调用方走单跳）。

    只有拆出 **两个以上** 子问题才算多跳：单个子问题等价于原问题的改写，那是 ``plan``
    节点的职责，在这里返回会白跑一轮扇出。
    """
    prompt = (
        "你是企业知识库检索规划器。把用户问题拆成互相独立、可分别检索的子问题，"
        "只做拆解，不回答问题，不引入原问题没有的实体。\n"
        f'输出 JSON：{{"sub_questions": ["子问题1", "子问题2"]}}，最多 {max_sub_questions} 条。\n'
        "若问题本身只问一件事，返回空列表。\n"
        f"原始问题：{_clip(question, 500)}"
    )
    try:
        raw = await llm_service.generate(
            prompt,
            temperature=0.0,
            action="agentic_rag_decompose",
            user_id=user_id,
        )
        payload = llm_service.parse_json_object(raw)
        if not isinstance(payload, dict):
            raise ValueError("invalid_decompose_json")
        candidates = payload.get("sub_questions")
        if not isinstance(candidates, list):
            raise ValueError("invalid_sub_questions")
    except Exception:
        return _unavailable("decompose_unavailable", sub_questions=[])

    normalized: list[str] = []
    for item in candidates:
        text = _clip(item, _SUB_QUESTION_MAX_CHARS)
        if not text or text == question.strip() or text in normalized:
            continue
        normalized.append(text)
        if len(normalized) >= max(int(max_sub_questions), 2):
            break
    return {"available": True, "sub_questions": normalized if len(normalized) >= 2 else []}


async def judge_evidence(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    user_id: int | None = None,
) -> dict[str, Any]:
    """判断已检索到的片段够不够回答问题，并说出还缺什么。

    ``missing`` 是这一步真正的产出：它会被拼进下一轮检索表达式，否则「判定证据不足」
    只是把同一个查询再跑一遍。
    """
    prompt = (
        "你是企业知识库证据审查员。只依据给定片段判断证据是否足以回答问题，"
        "不补充外部知识，不写答案。\n"
        '输出 JSON：{"sufficient": true, "score": 0.0, "missing": "还缺哪类信息，不超过40字"}\n'
        "score 表示证据支撑度（0-1）。证据足够时 missing 留空。\n\n"
        f"问题：{_clip(question, 500)}\n\n"
        f"{_evidence_block(chunks)}"
    )
    try:
        raw = await llm_service.generate(
            prompt,
            temperature=0.0,
            action="agentic_rag_evidence_judge",
            user_id=user_id,
        )
        payload = llm_service.parse_json_object(raw)
        if not isinstance(payload, dict) or "sufficient" not in payload:
            raise ValueError("invalid_judge_json")
    except Exception:
        return _unavailable("judge_unavailable", sufficient=None, score=None, missing="")

    try:
        score = round(max(0.0, min(float(payload.get("score")), 1.0)), 4)
    except (TypeError, ValueError):
        score = None
    return {
        "available": True,
        "sufficient": bool(payload.get("sufficient")),
        "score": score,
        "missing": _clip(payload.get("missing"), 80),
    }


async def check_faithfulness(
    question: str,
    answer: str,
    chunks: list[dict[str, Any]],
    *,
    user_id: int | None = None,
) -> dict[str, Any]:
    """生成后校验：答案里的事实性断言是否都能在片段中找到依据。

    与检索前的证据判分是两件事——证据够也可能生成出片段里没有的数字或期限，这一步专门
    拦那种「读起来很像、但片段里查不到」的答案。
    """
    prompt = (
        "你是企业知识库答案审查员。逐条检查回答中的事实性断言（数字、期限、金额、条件、"
        "责任主体）能否在片段中找到依据。片段之外的常识性表述不算不忠实。\n"
        '输出 JSON：{"faithful": true, "unsupported": ["缺依据的断言"], "reason": "不超过40字"}\n\n'
        f"问题：{_clip(question, 500)}\n"
        f"回答：{_clip(answer, _ANSWER_MAX_CHARS)}\n\n"
        f"{_evidence_block(chunks)}"
    )
    try:
        raw = await llm_service.generate(
            prompt,
            temperature=0.0,
            action="agentic_rag_faithfulness",
            user_id=user_id,
        )
        payload = llm_service.parse_json_object(raw)
        if not isinstance(payload, dict) or "faithful" not in payload:
            raise ValueError("invalid_faithfulness_json")
    except Exception:
        return _unavailable("faithfulness_unavailable", faithful=None, unsupported=[])

    unsupported = payload.get("unsupported")
    items = [_clip(item, 100) for item in unsupported[:_UNSUPPORTED_MAX_ITEMS]] if isinstance(unsupported, list) else []
    return {
        "available": True,
        "faithful": bool(payload.get("faithful")),
        "unsupported": [item for item in items if item],
        "reason": _clip(payload.get("reason"), 80),
    }
