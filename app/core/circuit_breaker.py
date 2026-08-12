"""供应商健康 / 熔断 / 半开恢复。

粒度：``供应商 + endpoint + 模型能力类型``（provider, base_url, task）。

- 状态机：closed -> open -> half_open -> closed/open。
- 仅可计入失败（timeout / transport / provider_5xx）驱动熔断；
  参数、鉴权、权限、内容拦截等不计入（说明供应商在线）。
- 熔断打开时 ``can_attempt`` 返回 False，调用方跳过该目标；冷却结束进入
  half_open，允许最多 ``half_open_max_concurrency`` 个探测请求；探测成功关闭，
  探测失败（可计入）重新打开。
- 默认进程内实现（``InMemoryCircuitBackend``），不跨多实例共享；
  ``RedisCircuitBackend`` 为可选后端（默认关闭），仅提供跨实例持久化/共享，
  不承诺强一致。无论是否启用 Redis，默认可运行。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Protocol

from app.core.config import get_settings
from app.core.model_policy import ModelErrorKind

settings = get_settings()

# 仅这些失败计入熔断；参数/鉴权/权限/内容拦截/限流不计入。
_COUNTING_KINDS = frozenset(
    {
        ModelErrorKind.TIMEOUT,
        ModelErrorKind.TRANSPORT,
        ModelErrorKind.PROVIDER_5XX,
    }
)


def counts_toward_breaker(kind: ModelErrorKind | str) -> bool:
    normalized = kind if isinstance(kind, ModelErrorKind) else ModelErrorKind(kind)
    return normalized in _COUNTING_KINDS


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _UnitState:
    state: str = CircuitState.CLOSED.value
    consecutive_failures: int = 0
    opened_at: float = 0.0
    half_open_probes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "opened_at": self.opened_at,
            "half_open_probes": self.half_open_probes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "_UnitState":
        if not data:
            return cls()
        return cls(
            state=str(data.get("state") or CircuitState.CLOSED.value),
            consecutive_failures=int(data.get("consecutive_failures") or 0),
            opened_at=float(data.get("opened_at") or 0.0),
            half_open_probes=int(data.get("half_open_probes") or 0),
        )


class CircuitBackend(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def set(self, key: str, data: dict[str, Any]) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...


class InMemoryCircuitBackend:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._store.get(key)

    def set(self, key: str, data: dict[str, Any]) -> None:
        self._store[key] = dict(data)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()


class RedisCircuitBackend:
    """可选 Redis 后端：状态以 JSON 存单 key，跨实例持久化/共享，非强一致。"""

    def __init__(self, redis_client: Any, prefix: str | None = None) -> None:
        self._client = redis_client
        self._prefix = prefix or settings.CIRCUIT_BREAKER_REDIS_PREFIX

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def get(self, key: str) -> dict[str, Any] | None:
        raw = self._client.get(self._key(key))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except (TypeError, ValueError):
            return None

    def set(self, key: str, data: dict[str, Any]) -> None:
        self._client.set(self._key(key), json.dumps(data))

    def delete(self, key: str) -> None:
        self._client.delete(self._key(key))

    def clear(self) -> None:
        for key in self._client.scan_iter(f"{self._prefix}:*"):
            self._client.delete(key)


class _NoopCircuitBreaker:
    """开关关闭时的空实现：始终放行，不维护状态。"""

    def key(self, *, provider: str, base_url: str, task: str) -> str:
        return f"{provider}|{base_url}|{task}"

    def state(self, key: str) -> str:
        return CircuitState.CLOSED.value

    def can_attempt(self, key: str) -> bool:
        return True

    def record_success(self, key: str) -> None:
        return None

    def record_failure(self, key: str, *, counts: bool) -> None:
        return None

    def reset(self) -> None:
        return None


class CircuitBreaker:
    def __init__(
        self,
        *,
        backend: CircuitBackend | None = None,
        failure_threshold: int | None = None,
        cooldown_seconds: float | None = None,
        half_open_max_concurrency: int | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold if failure_threshold is not None else int(settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD)
        self.cooldown_seconds = cooldown_seconds if cooldown_seconds is not None else float(settings.CIRCUIT_BREAKER_COOLDOWN_SECONDS)
        self.half_open_max_concurrency = half_open_max_concurrency if half_open_max_concurrency is not None else int(settings.CIRCUIT_BREAKER_HALF_OPEN_MAX_CONCURRENCY)
        self._backend = backend or InMemoryCircuitBackend()
        self._now = now or time.monotonic
        self._lock = threading.Lock()

    def key(self, *, provider: str, base_url: str, task: str) -> str:
        return f"{provider}|{base_url}|{task}"

    def state(self, key: str) -> str:
        with self._lock:
            return _UnitState.from_dict(self._backend.get(key)).state

    def can_attempt(self, key: str) -> bool:
        with self._lock:
            unit = _UnitState.from_dict(self._backend.get(key))
            if unit.state == CircuitState.CLOSED.value:
                return True
            if unit.state == CircuitState.OPEN.value:
                if self._now() - unit.opened_at >= self.cooldown_seconds:
                    unit.state = CircuitState.HALF_OPEN.value
                    unit.half_open_probes = 1
                    self._backend.set(key, unit.to_dict())
                    return True
                return False
            # half_open：受并发探测额度限制
            if unit.half_open_probes < self.half_open_max_concurrency:
                unit.half_open_probes += 1
                self._backend.set(key, unit.to_dict())
                return True
            return False

    def record_success(self, key: str) -> None:
        with self._lock:
            self._backend.set(key, _UnitState().to_dict())

    def record_failure(self, key: str, *, counts: bool) -> None:
        with self._lock:
            unit = _UnitState.from_dict(self._backend.get(key))
            if unit.state == CircuitState.CLOSED.value:
                if counts:
                    unit.consecutive_failures += 1
                    if unit.consecutive_failures >= self.failure_threshold:
                        unit.state = CircuitState.OPEN.value
                        unit.opened_at = self._now()
                        unit.consecutive_failures = 0
                else:
                    # 供应商返回了响应（4xx 等），在线 → 重置连续失败计数
                    unit.consecutive_failures = 0
            elif unit.state == CircuitState.HALF_OPEN.value:
                if counts:
                    unit.state = CircuitState.OPEN.value
                    unit.opened_at = self._now()
                    unit.consecutive_failures = 0
                    unit.half_open_probes = 0
                else:
                    # 半开探测获得响应（4xx 等），供应商在线 → 关闭
                    unit = _UnitState()
            self._backend.set(key, unit.to_dict())

    def reset(self) -> None:
        with self._lock:
            self._backend.clear()


def build_circuit_breaker() -> CircuitBreaker | _NoopCircuitBreaker:
    if not settings.CIRCUIT_BREAKER_ENABLED:
        return _NoopCircuitBreaker()
    if settings.CIRCUIT_BREAKER_REDIS_ENABLED:
        try:
            import redis

            client = redis.from_url(settings.REDIS_URL, decode_responses=True)
            return CircuitBreaker(backend=RedisCircuitBackend(client))
        except Exception:
            # Redis 不可用时回退进程内，保证默认可运行
            pass
    return CircuitBreaker()
