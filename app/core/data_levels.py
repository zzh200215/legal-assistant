"""统一数据分级模型（P0 出站数据保护）。

集中定义数据等级枚举与分级规则，业务代码禁止散落字符串判断：

- ``DataLevel``：public / internal / sensitive / highly_sensitive。
- ``base_level_for_action``：按 action 前缀给出安全默认基础等级
  （未知 action 一律 ``sensitive``，deny-by-default）。
- ``parse_level`` / ``level_rank`` / ``max_level``：等级解析、排序与取高。

等级语义：
- public：公开资料，无个人/商业敏感信息。
- internal：内部业务数据，允许发送给外部 LLM（PII 检测仍会升级）。
- sensitive：涉及个人或商业敏感信息，默认脱敏后才允许发送。
- highly_sensitive：极敏感数据，默认禁止发送；仅显式、可配置的受控策略放行。

内容升级规则（见 llm_outbound_gate）：命中任何 PII 规则 → 至少 sensitive；
命中 high/critical 严重度规则（身份证/银行卡/令牌/密码）→ highly_sensitive。
"""

from __future__ import annotations

from enum import Enum


class DataLevel(str, Enum):
    """统一数据等级。比较请使用 level_rank，禁止按字符串比较。"""

    PUBLIC = "public"
    INTERNAL = "internal"
    SENSITIVE = "sensitive"
    HIGHLY_SENSITIVE = "highly_sensitive"


_LEVEL_RANK = {
    DataLevel.PUBLIC: 0,
    DataLevel.INTERNAL: 1,
    DataLevel.SENSITIVE: 2,
    DataLevel.HIGHLY_SENSITIVE: 3,
}


def level_rank(level: DataLevel) -> int:
    """返回等级数值（public=0 … highly_sensitive=3）。"""
    return _LEVEL_RANK[level]


def parse_level(value: str | None) -> DataLevel | None:
    """把配置/外部值解析为 DataLevel；非法值返回 None（调用方按 deny-by-default 处理）。"""
    if value is None:
        return None
    try:
        return DataLevel(value.lower())
    except (ValueError, AttributeError):
        return None


def max_level(*levels: DataLevel | None) -> DataLevel | None:
    """取一组等级中的最高级；全空返回 None。"""
    present = [level for level in levels if level is not None]
    if not present:
        return None
    return max(present, key=level_rank)


# ── action → 基础等级默认映射（安全默认，可被 LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON 覆盖）────
# - public：embedding（纯向量化，无业务上下文）。
# - sensitive（前缀）：legal_*/document_*/meeting_*/email_*/task_*/agent_* 涉及业务敏感上下文。
# - internal：通用对话/生成类 action（chat/chat_stream/generate/generate_with_images/rag_*）。
# - 未知 action → sensitive（deny-by-default）。
_SENSITIVE_PREFIXES = (
    "legal_",
    "document_",
    "meeting_",
    "email_",
    "task_",
    "agent_",
)

_PUBLIC_ACTIONS = frozenset({"embedding"})

_INTERNAL_ACTIONS = frozenset({"chat", "chat_stream", "generate", "generate_with_images"})


def base_level_for_action(action: str) -> DataLevel:
    """按 action 给出安全默认基础等级（不含内容检测升级；未知 action → sensitive）。"""
    normalized = str(action or "").lower()
    if normalized in _PUBLIC_ACTIONS:
        return DataLevel.PUBLIC
    if normalized in _INTERNAL_ACTIONS or normalized.startswith("rag_"):
        return DataLevel.INTERNAL
    if normalized.startswith(_SENSITIVE_PREFIXES):
        return DataLevel.SENSITIVE
    return DataLevel.SENSITIVE
