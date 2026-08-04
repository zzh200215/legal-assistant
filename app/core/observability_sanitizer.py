import json
from typing import Any

SENSITIVE_LLM_ACTIONS = {
    "chat",
    "chat_stream",
    "generate",
    "generate_with_images",
    "rag_answer",
    "agent_plan",
    "agent_plan_preview",
    "document_summary",
    "document_risk_extract",
    "document_todo_extract",
    "document_clause_extract",
    "document_compare",
    "meeting_summary",
    "meeting_decision_extract",
    "meeting_topic_extract",
    "email_generate",
    "email_reply",
    "email_tone_switch",
    "email_thread_summary",
    "email_polish",
    "task_extract_from_chat",
    "task_decompose",
}


def truncate_text(text: str | None, limit: int = 2000) -> str | None:
    if text is None:
        return None
    return text[:limit]


def to_observability_excerpt(payload: Any) -> str | None:
    if payload is None:
        return None
    if isinstance(payload, str):
        return truncate_text(payload)
    try:
        return truncate_text(json.dumps(payload, ensure_ascii=False))
    except TypeError:
        return truncate_text(str(payload))


def sanitize_observability_excerpt(action: str, excerpt: str | None, *, kind: str) -> str | None:
    if excerpt is None:
        return None
    if action == "embedding":
        return truncate_text(excerpt)
    if action in SENSITIVE_LLM_ACTIONS:
        return json.dumps(
            {
                "redacted": True,
                "kind": kind,
                "action": action,
                "length": len(excerpt),
            },
            ensure_ascii=False,
        )
    return truncate_text(excerpt)


def sanitize_observability_error_message(action: str, error_message: str | None) -> str | None:
    if error_message is None:
        return None
    if action in SENSITIVE_LLM_ACTIONS:
        return json.dumps(
            {
                "redacted": True,
                "kind": "error",
                "action": action,
                "length": len(error_message),
            },
            ensure_ascii=False,
        )
    return truncate_text(error_message)


def sanitize_background_error_message(error_message: str | None) -> str | None:
    if error_message is None:
        return None
    return "任务执行失败，请查看系统日志"
