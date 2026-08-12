"""LLM 响应缓存：进程内 LRU + 可选 Redis，带 TTL。

键由调用方预先哈希为 digest（见 ModelGateway._cache_key），本类只负责按
prefix:digest 存取，不接触任何 prompt / 权限上下文原文——原文只参与网关侧
的 sha256 摘要计算，绝不写入键或日志。

- 默认纯进程内（多实例各持一份缓存，TTL 自然过期，不做跨实例失效）；
- ``LLM_RESPONSE_CACHE_REDIS_ENABLED=true`` 且 Redis 可用时跨实例共享
  （非强一致，无主动失效），Redis 不可用自动退化进程内。
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional

import redis

from app.core.config import get_settings

settings = get_settings()


class LLMResponseCache:
    def __init__(
        self,
        *,
        capacity: int,
        ttl_seconds: int,
        redis_enabled: bool,
        redis_prefix: str,
        now: Callable[[], float] | None = None,
    ):
        self.capacity = max(1, capacity)
        self.ttl_seconds = max(1, ttl_seconds)
        self.redis_enabled = redis_enabled
        self.redis_prefix = redis_prefix
        self._now = now or time.monotonic
        self._cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self._redis = None
        if redis_enabled:
            try:
                self._redis = redis.from_url(settings.REDIS_URL, decode_responses=False)
            except Exception:
                self._redis = None

    def _key(self, digest: str) -> str:
        return f"{self.redis_prefix}:{digest}"

    def get(self, key: str) -> Optional[str]:
        """命中且未过期返回缓存文本；否则 None（同时清除过期项）。"""
        now = self._now()
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                expires_at, value = entry
                if expires_at > now:
                    self._cache.move_to_end(key)
                    return value
                self._cache.pop(key, None)
        if self._redis is not None:
            try:
                raw = self._redis.get(self._key(key))
                if raw is not None:
                    value = json.loads(raw)
                    with self._lock:
                        self._cache[key] = (now + self.ttl_seconds, value)
                        self._cache.move_to_end(key)
                    return value
            except Exception:
                pass
        return None

    def put(self, key: str, value: str) -> None:
        now = self._now()
        with self._lock:
            self._cache[key] = (now + self.ttl_seconds, value)
            self._cache.move_to_end(key)
            while len(self._cache) > self.capacity:
                self._cache.popitem(last=False)
        if self._redis is not None:
            try:
                self._redis.setex(self._key(key), self.ttl_seconds, json.dumps(value))
            except Exception:
                pass

    def clear(self) -> None:
        """清空内存缓存（测试隔离用；Redis 中的键由 TTL 自然过期）。"""
        with self._lock:
            self._cache.clear()


def build_response_cache() -> LLMResponseCache:
    return LLMResponseCache(
        capacity=settings.LLM_RESPONSE_CACHE_CAPACITY,
        ttl_seconds=settings.LLM_RESPONSE_CACHE_TTL_SECONDS,
        redis_enabled=settings.LLM_RESPONSE_CACHE_REDIS_ENABLED,
        redis_prefix=settings.LLM_RESPONSE_CACHE_REDIS_PREFIX,
    )
