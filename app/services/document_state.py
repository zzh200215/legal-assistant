"""文档状态机：状态迁移集中定义，禁止业务代码随意直接改状态。

状态（Document.status 最终态）：
    主流程    uploaded -> parsing -> parsed -> chunking -> indexing -> indexed
    失败/恢复 parsing|chunking|indexing|parsed -> failed -> retrying -> 对应阶段
    降级      indexing -> parsed（索引失败但解析/切分已完成）
    重建      indexed -> parsing（版本/解析器/切分器变化后重跑）

说明：
- ``status`` 为最终状态，``current_stage`` 记录当前处理阶段（可含子阶段），
  ``failure_stage`` 记录最后一次失败发生的阶段。
- 历史遗留值 ``pending``/``processing`` 按别名映射为 ``uploaded``/``parsing``，
  使存量数据与新状态机一致，同时仍禁止非法跳转。
- 并发安全由调用方配合 ``Document.version`` 乐观锁（version_id_col）保证：
  并发 worker 先抢占状态，冲突时抛出并回滚，避免重复处理或状态回退。
"""

from __future__ import annotations

from app.core.time import utc_now

# 合法状态集合（含历史别名，最终写入一律使用规范值）
DOCUMENT_STATUS_UPLOADED = "uploaded"
DOCUMENT_STATUS_PARSING = "parsing"
DOCUMENT_STATUS_PARSED = "parsed"
DOCUMENT_STATUS_CHUNKING = "chunking"
DOCUMENT_STATUS_INDEXING = "indexing"
DOCUMENT_STATUS_INDEXED = "indexed"
DOCUMENT_STATUS_FAILED = "failed"
DOCUMENT_STATUS_RETRYING = "retrying"

# 历史遗留值别名：旧代码写入 pending/processing，新状态机按等价状态处理。
_LEGACY_ALIASES = {
    "pending": DOCUMENT_STATUS_UPLOADED,
    "processing": DOCUMENT_STATUS_PARSING,
}

# 规范化状态集合（业务最终值）
CANONICAL_STATUSES = frozenset(
    {
        DOCUMENT_STATUS_UPLOADED,
        DOCUMENT_STATUS_PARSING,
        DOCUMENT_STATUS_PARSED,
        DOCUMENT_STATUS_CHUNKING,
        DOCUMENT_STATUS_INDEXING,
        DOCUMENT_STATUS_INDEXED,
        DOCUMENT_STATUS_FAILED,
        DOCUMENT_STATUS_RETRYING,
    }
)

# 合法迁移边：当前状态 -> 可迁移到的目标状态集合
DOCUMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    DOCUMENT_STATUS_UPLOADED: frozenset({DOCUMENT_STATUS_PARSING, DOCUMENT_STATUS_FAILED}),
    DOCUMENT_STATUS_PARSING: frozenset({DOCUMENT_STATUS_PARSED, DOCUMENT_STATUS_FAILED}),
    DOCUMENT_STATUS_PARSED: frozenset(
        {DOCUMENT_STATUS_CHUNKING, DOCUMENT_STATUS_INDEXING, DOCUMENT_STATUS_FAILED, DOCUMENT_STATUS_PARSING}
    ),
    DOCUMENT_STATUS_CHUNKING: frozenset({DOCUMENT_STATUS_PARSED, DOCUMENT_STATUS_INDEXING, DOCUMENT_STATUS_FAILED}),
    DOCUMENT_STATUS_INDEXING: frozenset({DOCUMENT_STATUS_INDEXED, DOCUMENT_STATUS_PARSED, DOCUMENT_STATUS_FAILED}),
    DOCUMENT_STATUS_INDEXED: frozenset({DOCUMENT_STATUS_PARSING, DOCUMENT_STATUS_INDEXING}),
    DOCUMENT_STATUS_FAILED: frozenset({DOCUMENT_STATUS_RETRYING}),
    DOCUMENT_STATUS_RETRYING: frozenset(
        {DOCUMENT_STATUS_PARSING, DOCUMENT_STATUS_PARSED, DOCUMENT_STATUS_INDEXING, DOCUMENT_STATUS_FAILED}
    ),
}


class DocumentStateTransitionError(ValueError):
    """非法状态迁移：目标状态不在当前状态的合法迁移集合内。"""


def normalize_status(status: str | None) -> str:
    """把历史遗留状态值规范化为状态机内部值；未知值原样返回（由校验层拦截）。"""
    value = (status or DOCUMENT_STATUS_UPLOADED).strip()
    return _LEGACY_ALIASES.get(value, value)


def can_transition(current: str | None, target: str) -> bool:
    """判断 current -> target 是否为合法迁移。"""
    return target in DOCUMENT_TRANSITIONS.get(normalize_status(current), frozenset())


def transition_document(
    document,
    target: str,
    *,
    stage: str | None = None,
    failure_stage: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """集中状态迁移：校验合法性并写入状态/阶段/失败字段。

    不负责 commit —— 由调用方在事务边界统一提交，便于与乐观锁冲突处理联动。
    非法迁移抛出 DocumentStateTransitionError。
    """
    current = normalize_status(getattr(document, "status", None))
    if target not in DOCUMENT_TRANSITIONS.get(current, frozenset()):
        raise DocumentStateTransitionError(f"Illegal document state transition: {current} -> {target}")

    document.status = target
    document.current_stage = stage or target
    document.last_processed_at = utc_now()

    if target == DOCUMENT_STATUS_FAILED:
        document.failure_stage = failure_stage or getattr(document, "current_stage", None) or target
        document.error_code = error_code
        document.error_message = (error_message or "")[:1000] if error_message else None
    elif failure_stage or error_code or error_message:
        # 非 failed 但携带失败信息（如索引降级 → parsed）：仍记录失败阶段/原因。
        if failure_stage is not None:
            document.failure_stage = failure_stage
        if error_code is not None:
            document.error_code = error_code
        if error_message is not None:
            document.error_message = (error_message or "")[:1000] if error_message else None
    elif current == DOCUMENT_STATUS_FAILED:
        # 离开 failed：清理失败信息，进入 retrying/parsing 阶段
        document.failure_stage = None
        document.error_code = None
        document.error_message = None


def update_stage(document, stage: str) -> None:
    """集中更新“当前处理阶段”（不改变最终状态 status）。"""
    document.current_stage = stage
    document.last_processed_at = utc_now()
