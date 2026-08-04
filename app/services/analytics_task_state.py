import re


TASK_ID_PATTERN = re.compile(r"task_id=([^;\\s]+)")
MAX_LENGTH_PATTERN = re.compile(r"max_length=(\d+)")


def extract_task_id(detail: str | None) -> str | None:
    if not detail:
        return None
    match = TASK_ID_PATTERN.search(detail)
    return match.group(1) if match else None


def extract_max_length(detail: str | None, default: int = 500) -> int:
    if not detail:
        return default
    match = MAX_LENGTH_PATTERN.search(detail)
    if not match:
        return default
    try:
        return int(match.group(1))
    except ValueError:
        return default


def normalize_async_state(state: str | None, action: str | None) -> str:
    state = (state or "").upper()
    action = action or ""
    if state == "PENDING":
        return "pending"
    if state in {"STARTED", "PROCESSING", "RETRY"}:
        return "running"
    if state == "SUCCESS":
        return "succeeded"
    if state == "FAILURE":
        return "failed"
    if action.endswith("_submitted"):
        return "pending"
    if action.endswith("_started") or action.endswith("_retrying"):
        return "running"
    if action.endswith("_succeeded"):
        return "succeeded"
    if action.endswith("_failed"):
        return "failed"
    return "pending"
