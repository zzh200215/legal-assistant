from datetime import datetime, timezone

import redis

from app.core.config import get_settings


def record_beat_heartbeat() -> None:
    try:
        redis.from_url(get_settings().REDIS_URL).set(
            "aibg:operations:beat:last_tick",
            datetime.now(timezone.utc).isoformat(),
            ex=180,
        )
    except Exception:
        pass


def background_error_detail(
    task_id: str,
    *,
    retries: int | None = None,
    retry: int | None = None,
    countdown: int | None = None,
) -> str:
    parts = [f"task_id={task_id}"]
    if retry is not None:
        parts.append(f"retry={retry}")
    if retries is not None:
        parts.append(f"retries={retries}")
    if countdown is not None:
        parts.append(f"countdown={countdown}")
    parts.append("error=redacted")
    return "; ".join(parts)
