"""工作台首屏历史计数缓存。

三个 COUNT 很轻，但每个工作台进入都会打一次；用 5 秒进程内 TTL 缓存吸收
连续刷新/切 tab 的重复请求。只缓存计数，不缓存任何记录正文。

- 进程内、多 worker 各自一份；TTL 到期自然失效，不做跨实例主动失效。
- 新建记录后最多 5 秒内可能读到旧计数；前端 invalidation 会重取，但可能命中旧缓存。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable

from app.core.config import get_settings


class HistoryCountsCache:
    def __init__(
        self,
        *,
        ttl_seconds: int = 5,
        capacity: int = 2048,
        now: Callable[[], float] | None = None,
    ):
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.capacity = max(1, int(capacity))
        self._now = now or time.monotonic
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        if self.ttl_seconds <= 0:
            return None
        now = self._now()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at > now:
                self._cache.move_to_end(key)
                return dict(value)
            self._cache.pop(key, None)
        return None

    def put(self, key: str, value: dict) -> None:
        if self.ttl_seconds <= 0:
            return
        now = self._now()
        with self._lock:
            self._cache[key] = (now + self.ttl_seconds, dict(value))
            self._cache.move_to_end(key)
            while len(self._cache) > self.capacity:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


settings = get_settings()
history_counts_cache = HistoryCountsCache(
    ttl_seconds=settings.LEGAL_HISTORY_COUNTS_CACHE_TTL_SECONDS,
)
