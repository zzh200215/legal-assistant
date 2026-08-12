from datetime import datetime, timezone

import redis

from app.core.config import get_settings
from app.core.time import utc_now


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


def acquire_document_lock(document_id: int, ttl_seconds: int) -> bool:
    """每文档分布式锁（Redis SET NX PX）：并发 worker 只允许一个处理同一文档。

    Redis 不可用时返回 True（放行），由数据库乐观锁（Document.version CAS）兜底，
    不阻断处理。锁键不含文档正文/敏感内容。
    """
    try:
        client = redis.from_url(get_settings().REDIS_URL)
        return bool(client.set(f"aibg:doclock:{int(document_id)}", utc_now().isoformat(), nx=True, ex=ttl_seconds))
    except Exception:  # noqa: BLE001 - Redis 异常不阻断任务，交由乐观锁兜底
        return True


def release_document_lock(document_id: int) -> None:
    try:
        redis.from_url(get_settings().REDIS_URL).delete(f"aibg:doclock:{int(document_id)}")
    except Exception:  # noqa: BLE001
        pass
