"""图 checkpoint 时间旅行：列出一次 Run 的每个 superstep，并回放任意一步当时的状态。

在此之前 checkpoint 只有一个用途——从**最后**一个断点恢复（审批 resume / 快照恢复）。
历史 checkpoint 一直都在（保留窗口内），只是没有任何读取入口：排查时只能看
ToolCallLog 的结果，看不到「那一步图的状态长什么样、下一步本来要去哪、是不是停在中断上」。

只读：不重放执行、不 fork 分支。从历史 checkpoint 重新跑会把该点之后的工具调用再执行
一遍（写工具仍走审批闸，但审计线会分叉成两条），那是另一个决定，不在这里做。

输出经 ``redact_payload`` 收口：checkpoint 里存着完整对话与工具入参，直接回给客户端
等于开了一条绕过既有审计脱敏的旁路。工具入参按契约声明的 ``sensitive_fields`` 掩码。
"""

from __future__ import annotations

from typing import Any

from app.core.observability_sanitizer import redact_payload

# 单次返回的 superstep 上限：一次 Run 最多 max_steps 步，每步若干 checkpoint，
# 取 200 足够覆盖一次完整 Run，又不至于把整个 thread 一次性拉进内存。
CHECKPOINT_HISTORY_LIMIT = 200

# 观察值/原始决策文本只给预览，避免把整段工具输出和 LLM 原文搬进响应。
_PREVIEW_CHARS = 200

# 直接透出的标量通道：都是控制流事实，没有用户内容。
_SCALAR_KEYS = (
    "step",
    "max_steps",
    "retry_count",
    "current_action_type",
    "current_tool_name",
    "current_worker_agent",
    "master_agent",
    "worker_agent",
    "worker_index",
    "awaiting_approval",
    "pending_approval_request_id",
    "timed_out",
    "handoff_pending",
    "handoff_target_worker",
    "evidence_scope_seen",
    "needs_evidence_verification",
    "verification_target",
    "parallel_pending",
)


def _preview(value: Any) -> str | None:
    """截断 + 脱敏的文本预览；空值返回 None。"""
    if not isinstance(value, str) or not value:
        return None
    text = value if len(value) <= _PREVIEW_CHARS else f"{value[:_PREVIEW_CHARS]}…"
    redacted = redact_payload(text, max_text_len=_PREVIEW_CHARS * 2)
    return redacted if isinstance(redacted, str) else None


def _tool_input_digest(tool_name: Any, raw_input: Any) -> dict[str, Any] | None:
    """工具入参脱敏：契约声明的 sensitive_fields 整值掩码，其余走默认 deny 规则。"""
    if not isinstance(raw_input, dict) or not raw_input:
        return None
    sensitive: set[str] = set()
    if isinstance(tool_name, str) and tool_name:
        try:
            from app.mcp.registry import mcp_registry
            from app.mcp.tool_contract import resolve_contract

            tool = mcp_registry.get_tool(tool_name)
            if tool is not None:
                sensitive = {str(item) for item in resolve_contract(tool).sensitive_fields}
        except Exception:  # noqa: BLE001 - 取不到契约时按「全部未知键」处理，更严格
            sensitive = set()
    digest = redact_payload(raw_input, sensitive_keys=sensitive or None)
    return digest if isinstance(digest, dict) else None


def state_digest(values: Any) -> dict[str, Any]:
    """把一份 checkpoint state 压成可安全外传的摘要。

    白名单式：只挑控制流通道 + 少量脱敏预览。新增 state 通道不会自动出现在这里，
    这是刻意的——通道随需求增长，默认不外传比默认外传安全。
    """
    if not isinstance(values, dict):
        return {}
    digest: dict[str, Any] = {key: values[key] for key in _SCALAR_KEYS if key in values}
    messages = values.get("messages")
    if isinstance(messages, list):
        digest["messages_count"] = len(messages)
    observation = _preview(values.get("last_observation"))
    if observation:
        digest["observation_preview"] = observation
    raw_decision = _preview(values.get("current_raw"))
    if raw_decision:
        digest["decision_preview"] = raw_decision
    tool_input = _tool_input_digest(values.get("current_tool_name"), values.get("current_safe_input"))
    if tool_input:
        digest["tool_input"] = tool_input
    parallel_results = values.get("parallel_results")
    if isinstance(parallel_results, dict) and parallel_results:
        digest["parallel_branches"] = sorted(str(key) for key in parallel_results)
    return digest


def _checkpoint_id(config: Any) -> str | None:
    if not isinstance(config, dict):
        return None
    value = (config.get("configurable") or {}).get("checkpoint_id")
    return str(value) if value else None


def _is_internal_node(name: str) -> bool:
    """langgraph 的 __start__/__end__ 哨兵不是业务节点，不进 wrote 视图。"""
    return name.startswith("__") and name.endswith("__")


def _wrote_nodes_from_parent(parent: Any) -> list[str]:
    """「哪些业务节点执行后生成了当前 checkpoint」= 父快照下一步要执行的节点。"""
    if parent is None:
        return []
    return [str(node) for node in (getattr(parent, "next", ()) or ()) if not _is_internal_node(node)]


def _snapshot_entry(snapshot: Any, wrote_nodes: list[str] | None = None) -> dict[str, Any]:
    """把 LangGraph 的 StateSnapshot 转成一条历史记录。

    wrote_nodes 由调用方传入：langgraph 1.2.6 的历史快照 metadata 里没有 writes 键，
    只能沿父快照的 next 反推这一步步由谁写入。
    """
    metadata = getattr(snapshot, "metadata", None) or {}
    return {
        "checkpoint_id": _checkpoint_id(getattr(snapshot, "config", None)),
        "parent_checkpoint_id": _checkpoint_id(getattr(snapshot, "parent_config", None)),
        "created_at": getattr(snapshot, "created_at", None),
        # LangGraph 的 superstep 序号（-1 = 输入，0 起为循环步），与业务 step 不是一回事。
        "graph_step": metadata.get("step") if isinstance(metadata, dict) else None,
        "source": metadata.get("source") if isinstance(metadata, dict) else None,
        # 输入步（最旧）没有父快照，wrote_nodes 为空。
        "wrote_nodes": wrote_nodes or [],
        # 图当时准备去的下一个节点；空 = 该 checkpoint 是终态。
        "next_nodes": [str(item) for item in (getattr(snapshot, "next", ()) or ())],
        "interrupted": bool(getattr(snapshot, "interrupts", ()) or ()),
        "state": state_digest(getattr(snapshot, "values", None)),
    }


def supports_time_travel(workflow: Any) -> bool:
    """回退引擎没有 checkpoint，历史无从谈起。"""
    return callable(getattr(workflow, "get_state_history", None))


def checkpoint_history(
    workflow: Any,
    config: dict[str, Any],
    *,
    limit: int = CHECKPOINT_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    """按时间倒序列出该 thread 的 checkpoint（最新在前）。

    checkpoint 不可读（已被保留窗口清理、saver 不可用）时返回空列表而不是抛错：
    对调用方来说「没有历史」和「历史已过期」都只是没得看。
    """
    if not supports_time_travel(workflow):
        return []
    capped = max(1, min(int(limit), CHECKPOINT_HISTORY_LIMIT))
    try:
        snapshots = list(workflow.get_state_history(config, limit=capped))
    except Exception:  # noqa: BLE001 - checkpoint 不可读时退化为「无历史」
        return []
    # 结果最新在前：每个快照的「写入者」是它紧邻的更旧一个快照（即它的父）要去执行的节点；
    # 最旧的输入步没有父，wrote_nodes 为空。
    return [
        _snapshot_entry(item, _wrote_nodes_from_parent(snapshots[index + 1] if index + 1 < len(snapshots) else None))
        for index, item in enumerate(snapshots)
    ]


def checkpoint_state(
    workflow: Any,
    config: dict[str, Any],
    checkpoint_id: str,
) -> dict[str, Any] | None:
    """回放单个 checkpoint 当时的状态；不存在或不可读返回 None。

    thread_id 由调用方按 Run 拼好，checkpointer 按 (thread_id, checkpoint_id) 取，
    所以拿别的 Run 的 checkpoint_id 过来查不到东西——越权读天然被 thread 隔离挡住。
    """
    if not supports_time_travel(workflow) or not checkpoint_id:
        return None
    configurable = dict(config.get("configurable") or {})
    configurable["checkpoint_id"] = str(checkpoint_id)
    scoped = {**config, "configurable": configurable}
    try:
        snapshot = workflow.get_state(scoped)
    except Exception:  # noqa: BLE001 - 同 checkpoint_history：不可读即视为不存在
        return None
    # langgraph 对不存在的 checkpoint_id 不抛错，而是回显请求 id 返回 metadata/created_at
    # 皆为空的合成快照——所以不能靠 config 里的 id 判别，要看有没有真实 checkpoint 数据。
    if snapshot is None or getattr(snapshot, "metadata", None) is None or getattr(snapshot, "created_at", None) is None:
        return None
    wrote_nodes: list[str] = []
    parent_config = getattr(snapshot, "parent_config", None)
    if parent_config:
        try:
            wrote_nodes = _wrote_nodes_from_parent(workflow.get_state(parent_config))
        except Exception:  # noqa: BLE001 - 取不到父快照时退化为空 wrote
            wrote_nodes = []
    return _snapshot_entry(snapshot, wrote_nodes)
