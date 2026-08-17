"""RAG① 检索缓存：内容寻址嵌入缓存（sha256(model:text)），LRU + 可选 Redis。

键带模型盐（EMBEDDING_MODEL）——换模型即自失效，无需显式失效。
Redis 关闭或不可用时自动退化为纯内存 LRU。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from collections import OrderedDict
from typing import Callable, Optional

import redis

from app.core.config import get_settings

settings = get_settings()


class RagEmbeddingCache:
    def __init__(self, *, capacity: int, redis_enabled: bool, ttl_seconds: int,
                 redis_prefix: str, model: str):
        self.capacity = max(1, capacity)
        self.redis_enabled = redis_enabled
        self.ttl_seconds = max(1, ttl_seconds)
        self.redis_prefix = redis_prefix
        self.model = model
        self._cache: OrderedDict[str, list] = OrderedDict()
        self._lock = threading.Lock()
        self._redis = None
        if redis_enabled:
            try:
                self._redis = redis.from_url(settings.REDIS_URL, decode_responses=False)
            except Exception:
                self._redis = None

    def _key(self, text: str) -> str:
        digest = hashlib.sha256(f"{self.model}:{text}".encode("utf-8")).hexdigest()
        return f"{self.redis_prefix}:{digest}"

    def get(self, text: str) -> Optional[list]:
        key = self._key(text)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        if self._redis is not None:
            try:
                raw = self._redis.get(key)
                if raw is not None:
                    value = json.loads(raw)
                    with self._lock:
                        self._cache[key] = value
                        self._cache.move_to_end(key)
                    return value
            except Exception:
                pass
        return None

    def put(self, text: str, embedding: list) -> None:
        key = self._key(text)
        with self._lock:
            self._cache[key] = embedding
            self._cache.move_to_end(key)
            while len(self._cache) > self.capacity:
                self._cache.popitem(last=False)
        if self._redis is not None:
            try:
                self._redis.setex(key, self.ttl_seconds, json.dumps(embedding))
            except Exception:
                pass

    def remove(self, text: str) -> None:
        key = self._key(text)
        with self._lock:
            self._cache.pop(key, None)
        if self._redis is not None:
            try:
                self._redis.delete(key)
            except Exception:
                pass

    def clear(self) -> None:
        """清空内存缓存（测试隔离用；Redis 中的键保留，由 TTL 自然过期）。"""
        with self._lock:
            self._cache.clear()

    async def get_or_compute_batch(
        self,
        texts: list[str],
        compute_async: Callable[[list[str]], "asyncio.Future[list[list]]"],
    ) -> list[list]:
        """批量嵌入：命中返回缓存，未命中合并为一次 compute_async(miss_texts) 并写回。

        返回与 texts 顺序对齐的嵌入列表。compute_async 期望接收 miss 文本列表、返回嵌入列表。
        """
        embeddings: list[Optional[list]] = [None] * len(texts)
        missing_idx: list[int] = []
        missing_texts: list[str] = []
        for index, text in enumerate(texts):
            cached = self.get(text)
            if cached is not None:
                embeddings[index] = cached
            else:
                missing_idx.append(index)
                missing_texts.append(text)
        if missing_texts:
            computed = await compute_async(missing_texts)
            for index, embedding in zip(missing_idx, computed):
                self.put(texts[index], embedding)
                embeddings[index] = embedding
        return embeddings


def build_embedding_cache() -> RagEmbeddingCache:
    return RagEmbeddingCache(
        capacity=settings.RAG_EMBED_CACHE_CAPACITY,
        redis_enabled=settings.RAG_EMBED_CACHE_REDIS_ENABLED,
        ttl_seconds=settings.RAG_EMBED_CACHE_TTL_SECONDS,
        redis_prefix=settings.RAG_EMBED_CACHE_REDIS_PREFIX,
        model=settings.EMBEDDING_MODEL,
    )


rag_embedding_cache = build_embedding_cache()
