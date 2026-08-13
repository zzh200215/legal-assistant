from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Lua CAS 脚本：只有持有 token 才删除/续租，避免误删其他 worker 已续租或重获的锁。
_CAS_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""
_CAS_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""


def record_beat_heartbeat() -> None:
    try:
        redis.from_url(get_settings().REDIS_URL).set(
            "aibg:operations:beat:last_tick",
            datetime.now(timezone.utc).isoformat(),
            ex=180,
        )
    except Exception:  # noqa: BLE001 - 心跳失败不影响任务
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


def _lock_key(task_name: str, scope: str, window: str | None) -> str:
    """锁 key 规则：aibg:tasklock:{task_name}[:{scope}][:{window}]

    scope 携带租户/连接器/业务范围（如 tenant:12 / conn:3）；window 仅在需要按
    时间窗区分批次时使用（如日/小时作业桶）。键不含任何正文/敏感内容。
    """
    parts = [task_name]
    if scope:
        parts.append(scope)
    if window:
        parts.append(window)
    return "aibg:tasklock:" + ":".join(parts)


def _new_token() -> str:
    return uuid.uuid4().hex


def _client(redis_client: Any | None) -> Any:
    return redis_client or redis.from_url(get_settings().REDIS_URL)


def acquire_task_lock(
    task_name: str,
    *,
    scope: str = "",
    window: str | None = None,
    ttl_seconds: int,
    token: str | None = None,
    redis_client: Any | None = None,
) -> str | None:
    """获取分布式锁（SET NX PX）。成功返回 token，未获锁返回 None。

    Redis 不可用时返回 token（放行），由 DB 唯一约束/乐观锁/状态机兜底，
    不阻断任务（与文档锁既有语义一致）。
    """
    token = token or _new_token()
    key = _lock_key(task_name, scope, window)
    try:
        ok = bool(_client(redis_client).set(key, token, nx=True, ex=ttl_seconds))
        return token if ok else None
    except Exception:  # noqa: BLE001 - Redis 异常不阻断任务
        return token


def release_task_lock(
    task_name: str,
    *,
    scope: str = "",
    window: str | None = None,
    token: str | None = None,
    redis_client: Any | None = None,
) -> None:
    """CAS 释放：只有当前锁仍由本 token 持有时才删除，绝不误删他 worker 的锁。"""
    if not token:
        return
    key = _lock_key(task_name, scope, window)
    try:
        _client(redis_client).eval(_CAS_DELETE, 1, key, token)
    except Exception:  # noqa: BLE001
        logger.warning("release_task_lock failed for %s (scope=%r)", key, scope, exc_info=True)


def renew_task_lock(
    task_name: str,
    *,
    scope: str = "",
    window: str | None = None,
    token: str | None = None,
    ttl_seconds: int,
    redis_client: Any | None = None,
) -> bool:
    """心跳续租：token 匹配才延长 TTL，避免续租已被他 worker 重获的锁。"""
    if not token:
        return False
    key = _lock_key(task_name, scope, window)
    try:
        return bool(_client(redis_client).eval(_CAS_RENEW, 1, key, token, ttl_seconds * 1000))
    except Exception:  # noqa: BLE001
        return False


def run_locked(
    task_name: str,
    *,
    scope: str = "",
    window: str | None = None,
    ttl_seconds: int,
    fn: Callable[..., Any],
    on_skip: Callable[[], Any] | None = None,
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> Any:
    """在分布式锁内执行 fn；未获锁时调用 on_skip（记录日志安全跳过）并返回 None。

    释放走 token CAS；Redis 不可用时放行（DB 兜底），与文档锁语义一致。
    """
    token = acquire_task_lock(task_name, scope=scope, window=window, ttl_seconds=ttl_seconds)
    if token is None:
        if on_skip is not None:
            on_skip()
        return None
    try:
        return fn(*args, **(kwargs or {}))
    finally:
        release_task_lock(task_name, scope=scope, window=window, token=token)


def beat_lock(
    task_name: str,
    *,
    ttl_seconds: int,
    scope: str = "",
    window: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Celery beat 任务装饰器：固定 key + TTL 互斥（多实例 Beat 不重复执行、同任务不重叠）。

    未获锁时记录可观测日志并安全返回（不抛无意义异常）。
    """
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            def _skip() -> None:
                key = _lock_key(task_name, scope, window)
                logger.info("beat task %s skipped: lock held by another worker (key=%s)", task_name, key)

            return run_locked(
                task_name,
                scope=scope,
                window=window,
                ttl_seconds=ttl_seconds,
                fn=fn,
                on_skip=_skip,
                args=args,
                kwargs=kwargs,
            )
        return wrapper
    return decorator


# ── 文档锁兼容层（修复：释放改为 token CAS，不再无条件 DELETE）──────────────

# 进程内 token 台账：同一 worker 内 acquire/release 配对；跨 worker 由 TTL 兜底。
_DOC_LOCK_TOKENS: dict[int, str] = {}


def acquire_document_lock(document_id: int, ttl_seconds: int, *, redis_client: Any | None = None) -> bool:
    """每文档分布式锁（Redis SET NX PX）：并发 worker 只允许一个处理同一文档。

    Redis 不可用时返回 True（放行），由数据库乐观锁（Document.version CAS）兜底。
    返回 True 时在进程内登记 token，供 release_document_lock 做 CAS 释放。
    """
    token = acquire_task_lock(
        "document", scope=f"doc:{int(document_id)}", ttl_seconds=ttl_seconds, redis_client=redis_client,
    )
    if token is not None:
        _DOC_LOCK_TOKENS[int(document_id)] = token
        return True
    return False


def release_document_lock(document_id: int, *, redis_client: Any | None = None) -> None:
    """释放文档锁：仅当锁仍由本进程获取的 token 持有时删除（CAS），不误删他 worker。"""
    token = _DOC_LOCK_TOKENS.pop(int(document_id), None)
    if token is None:
        return
    release_task_lock("document", scope=f"doc:{int(document_id)}", token=token, redis_client=redis_client)
