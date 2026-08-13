"""外部调用统一韧性层：错误分类 + 指数退避重试 + 熔断 + 可观测日志。

适用范围：外部 HTTP / SMTP 等出站调用（webhook、飞书、运营告警、邮件）。

- 错误分类（``ExternalErrorKind``）：可重试 = 网络/连接/超时/限流/服务端 5xx；
  不可重试 = 参数/鉴权/权限/不存在/校验（4xx 说明服务在线）。
- 外部写（POST/PUT/PATCH）超时 → ``AMBIGUOUS_SIDE_EFFECT``，**不盲目重试**
  （先查询或幂等键确认是否已生效）。
- 熔断复用 ``app.core.circuit_breaker.CircuitBreaker`` 状态机，键
  ``external:{service}|{connector_id}|{op}`` 按服务/连接器隔离；仅可重试类计熔断。
- 日志复用 observability 约定：只记 trace_id/connector/op/duration/error_category/attempt，
  绝不记录 token / Authorization / 密钥 / 完整正文 / 请求 URL。
"""

from __future__ import annotations

import logging
import random
import socket
import smtplib
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# 写操作：超时意味着不确定是否已生效，不能盲目重试。
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 可能已生效的瞬时错误（写操作超时）→ 不重试。
_UNCERTAIN_KINDS = frozenset({"timeout"})


class ExternalErrorKind(str, Enum):
    NETWORK = "network"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMITED = "rate_limited"
    SERVER_5XX = "server_5xx"
    AMBIGUOUS_SIDE_EFFECT = "ambiguous_side_effect"
    AUTH = "auth"
    PERMISSION = "permission"
    PARAMS = "params"
    NOT_FOUND = "not_found"
    VALIDATION = "validation"
    CIRCUIT_OPEN = "circuit_open"


RETRYABLE_KINDS = frozenset(
    {
        ExternalErrorKind.NETWORK,
        ExternalErrorKind.TIMEOUT,
        ExternalErrorKind.CONNECTION,
        ExternalErrorKind.RATE_LIMITED,
        ExternalErrorKind.SERVER_5XX,
    }
)
# 计入熔断的失败：仅可重试类中的健康信号（限流不计入——说明服务在线，只是要慢下来）。
BREAKER_COUNTING_KINDS = frozenset(
    {
        ExternalErrorKind.NETWORK,
        ExternalErrorKind.TIMEOUT,
        ExternalErrorKind.CONNECTION,
        ExternalErrorKind.SERVER_5XX,
    }
)


@dataclass
class ExternalError(Exception):
    """分类后的外部调用错误。``retryable``/``counts_toward_breaker`` 由 kind 推导。"""

    kind: ExternalErrorKind
    message: str
    exc_type: str | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None
    attempts: int = 0
    circuit_key: str | None = None

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS

    @property
    def counts_toward_breaker(self) -> bool:
        return self.kind in BREAKER_COUNTING_KINDS


def classify_status_code(status_code: int) -> ExternalErrorKind:
    if status_code == 429:
        return ExternalErrorKind.RATE_LIMITED
    if status_code == 401:
        return ExternalErrorKind.AUTH
    if status_code == 403:
        return ExternalErrorKind.PERMISSION
    if status_code == 404:
        return ExternalErrorKind.NOT_FOUND
    if status_code == 422:
        return ExternalErrorKind.VALIDATION
    if 400 <= status_code < 500:
        return ExternalErrorKind.PARAMS
    if 500 <= status_code < 600:
        return ExternalErrorKind.SERVER_5XX
    return ExternalErrorKind.NETWORK


def classify_exception(exc: Exception) -> ExternalErrorKind:
    """按异常类型分类（无响应对象的传输/超时错误）。httpx / requests / smtplib / socket。"""
    # httpx（AsyncClient 与同步 client 共用异常层级）
    try:
        import httpx
    except ImportError:  # pragma: no cover - 依赖存在时才有意义
        httpx = None
    if httpx is not None:
        if isinstance(exc, httpx.TimeoutException):
            return ExternalErrorKind.TIMEOUT
        if isinstance(exc, httpx.ConnectError):
            return ExternalErrorKind.CONNECTION
        if isinstance(exc, httpx.NetworkError):
            return ExternalErrorKind.NETWORK
    # requests（历史调用点兜底）
    try:
        import requests
    except ImportError:  # pragma: no cover
        requests = None
    if requests is not None:
        if isinstance(exc, requests.exceptions.Timeout):
            return ExternalErrorKind.TIMEOUT
        if isinstance(exc, requests.exceptions.ConnectionError):
            return ExternalErrorKind.CONNECTION
        if isinstance(exc, requests.exceptions.RequestException):
            return ExternalErrorKind.NETWORK
    # smtplib / socket / OSError 层
    if isinstance(exc, TimeoutError):  # 含 socket.timeout（3.10+ 别名）
        return ExternalErrorKind.TIMEOUT
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, BrokenPipeError, socket.gaierror)):
        return ExternalErrorKind.CONNECTION
    if isinstance(exc, smtplib.SMTPException):
        return ExternalErrorKind.SERVER_5XX
    if isinstance(exc, OSError):
        return ExternalErrorKind.NETWORK
    return ExternalErrorKind.VALIDATION


def _response_of(exc: Exception) -> Any:
    return getattr(exc, "response", None)


def _status_of(exc: Exception) -> int | None:
    response = _response_of(exc)
    return getattr(response, "status_code", None)


def _retry_after_of(exc: Exception) -> float | None:
    response = _response_of(exc)
    headers = getattr(response, "headers", None)
    raw = headers.get("Retry-After") if headers is not None else None
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        # HTTP-date 格式的 Retry-After 忽略（退避兜底）
        return None


def classify_http_error(exc: Exception, *, method: str | None = None) -> ExternalError:
    """把任意外部调用异常分类为 ``ExternalError``（含写操作超时降级为 AMBIGUOUS）。"""
    status = _status_of(exc)
    kind = classify_status_code(status) if status is not None else classify_exception(exc)
    if method and method.upper() in WRITE_METHODS and kind.value in _UNCERTAIN_KINDS:
        kind = ExternalErrorKind.AMBIGUOUS_SIDE_EFFECT
    return ExternalError(
        kind=kind,
        message=f"external call failed: {kind.value}",
        exc_type=type(exc).__name__,
        status_code=status,
        retry_after_seconds=_retry_after_of(exc),
    )


def compute_backoff_delay(
    attempt: int,
    *,
    base_seconds: float,
    jitter: bool,
    max_wait_seconds: float,
    retry_after_seconds: float | None = None,
    respect_retry_after: bool = True,
) -> float:
    """指数退避 + 全抖动；尊重 ``Retry-After``（封顶 max_wait_seconds）。"""
    if retry_after_seconds is not None and respect_retry_after:
        return min(float(retry_after_seconds), max_wait_seconds)
    exponential = min(base_seconds * (2 ** (attempt - 1)), max_wait_seconds)
    if jitter:
        half = exponential / 2
        return half + random.uniform(0.0, half)
    return exponential


def call_with_retry(
    fn: Callable[[], Any],
    *,
    method: str = "GET",
    circuit_key: str | None = None,
    breaker: Any | None = None,
    on_attempt: Callable[[int], None] | None = None,
    classify: Callable[[Exception], ExternalError] = classify_http_error,
    max_attempts: int | None = None,
    max_wait_seconds: float | None = None,
    backoff_base_seconds: float | None = None,
    jitter: bool | None = None,
    respect_retry_after: bool | None = None,
) -> Any:
    """带熔断 + 退避重试地执行外部调用；耗尽或不可重试时抛 ``ExternalError``。

    ``breaker`` 暴露 ``can_attempt/record_failure/record_success``（复用 CircuitBreaker）。
    """
    settings = get_settings()
    max_attempts = max_attempts if max_attempts is not None else int(settings.EXTERNAL_MAX_ATTEMPTS)
    max_wait_seconds = max_wait_seconds if max_wait_seconds is not None else float(settings.EXTERNAL_MAX_WAIT_SECONDS)
    backoff_base = backoff_base_seconds if backoff_base_seconds is not None else float(settings.EXTERNAL_BACKOFF_BASE_SECONDS)
    use_jitter = jitter if jitter is not None else bool(settings.EXTERNAL_BACKOFF_JITTER)
    use_retry_after = respect_retry_after if respect_retry_after is not None else bool(settings.EXTERNAL_RESPECT_RETRY_AFTER)

    attempt = 0
    while True:
        attempt += 1
        if circuit_key and breaker is not None and not breaker.can_attempt(circuit_key):
            raise ExternalError(
                kind=ExternalErrorKind.CIRCUIT_OPEN,
                message=f"external call circuit open for {circuit_key}",
                circuit_key=circuit_key,
                attempts=attempt,
            )
        if on_attempt is not None:
            on_attempt(attempt)
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - 统一分类后按类型决定重试
            err = classify(exc, method=method) if _wants_method(classify) else classify(exc)
            err.attempts = attempt
            err.circuit_key = circuit_key
            if circuit_key and breaker is not None:
                breaker.record_failure(circuit_key, counts=err.counts_toward_breaker)
            if not err.retryable or attempt >= max_attempts:
                raise err
            delay = compute_backoff_delay(
                attempt,
                base_seconds=backoff_base,
                jitter=use_jitter,
                max_wait_seconds=max_wait_seconds,
                retry_after_seconds=err.retry_after_seconds,
                respect_retry_after=use_retry_after,
            )
            time.sleep(delay)
        else:
            if circuit_key and breaker is not None:
                breaker.record_success(circuit_key)
            return result


def _wants_method(classify: Callable[..., Any]) -> bool:
    import inspect

    try:
        return "method" in inspect.signature(classify).parameters
    except (TypeError, ValueError):  # 内置/不可内省 callable
        return False


async def acall_with_retry(
    fn: Callable[[], Any],
    *,
    method: str = "GET",
    circuit_key: str | None = None,
    breaker: Any | None = None,
    on_attempt: Callable[[int], None] | None = None,
    classify: Callable[[Exception], ExternalError] = classify_http_error,
    max_attempts: int | None = None,
    max_wait_seconds: float | None = None,
    backoff_base_seconds: float | None = None,
    jitter: bool | None = None,
    respect_retry_after: bool | None = None,
) -> Any:
    """async 版 call_with_retry：供 httpx.AsyncClient 等异步外部调用复用同一分类/退避/熔断。"""
    import asyncio

    settings = get_settings()
    max_attempts = max_attempts if max_attempts is not None else int(settings.EXTERNAL_MAX_ATTEMPTS)
    max_wait_seconds = max_wait_seconds if max_wait_seconds is not None else float(settings.EXTERNAL_MAX_WAIT_SECONDS)
    backoff_base = backoff_base_seconds if backoff_base_seconds is not None else float(settings.EXTERNAL_BACKOFF_BASE_SECONDS)
    use_jitter = jitter if jitter is not None else bool(settings.EXTERNAL_BACKOFF_JITTER)
    use_retry_after = respect_retry_after if respect_retry_after is not None else bool(settings.EXTERNAL_RESPECT_RETRY_AFTER)

    attempt = 0
    while True:
        attempt += 1
        if circuit_key and breaker is not None and not breaker.can_attempt(circuit_key):
            raise ExternalError(
                kind=ExternalErrorKind.CIRCUIT_OPEN,
                message=f"external call circuit open for {circuit_key}",
                circuit_key=circuit_key,
                attempts=attempt,
            )
        if on_attempt is not None:
            on_attempt(attempt)
        try:
            result = await fn()
        except Exception as exc:  # noqa: BLE001 - 统一分类后按类型决定重试
            err = classify(exc, method=method) if _wants_method(classify) else classify(exc)
            err.attempts = attempt
            err.circuit_key = circuit_key
            if circuit_key and breaker is not None:
                breaker.record_failure(circuit_key, counts=err.counts_toward_breaker)
            if not err.retryable or attempt >= max_attempts:
                raise err
            delay = compute_backoff_delay(
                attempt,
                base_seconds=backoff_base,
                jitter=use_jitter,
                max_wait_seconds=max_wait_seconds,
                retry_after_seconds=err.retry_after_seconds,
                respect_retry_after=use_retry_after,
            )
            await asyncio.sleep(delay)
        else:
            if circuit_key and breaker is not None:
                breaker.record_success(circuit_key)
            return result


def log_external_call(record: dict) -> None:
    """可观测日志：仅元数据，绝不记录 token / Authorization / 密钥 / 完整正文 / URL。"""
    logger.info(
        "external_call service=%s connector=%s op=%s method=%s duration_ms=%s error=%s attempt=%s",
        record.get("service"),
        record.get("connector_id") or "-",
        record.get("op"),
        record.get("method") or "GET",
        record.get("duration_ms"),
        record.get("error_category") or "ok",
        record.get("attempt") or 0,
    )


class ExternalResilience:
    """外部调用统一入口：熔断键 + 调用记录 + 配置默认值。

    ``key(service, connector_id, op)`` 生产 ``external:{service}|{connector_id}|{op}``，
    按服务/连接器隔离熔断与可观测性。
    """

    def __init__(self, *, breaker: Any | None = None) -> None:
        settings = get_settings()
        self._breaker = breaker
        if settings.EXTERNAL_CIRCUIT_BREAKER_ENABLED and breaker is None:
            from app.core.circuit_breaker import CircuitBreaker

            self._breaker = CircuitBreaker(
                failure_threshold=int(settings.EXTERNAL_CIRCUIT_FAILURE_THRESHOLD),
                cooldown_seconds=float(settings.EXTERNAL_CIRCUIT_COOLDOWN_SECONDS),
                half_open_max_concurrency=int(settings.EXTERNAL_CIRCUIT_HALF_OPEN_MAX_CONCURRENCY),
            )

    @staticmethod
    def key(*, service: str, connector_id: int | None = None, op: str) -> str:
        return f"external:{service}|{connector_id if connector_id is not None else '-'}|{op}"

    def call(
        self,
        fn: Callable[[], Any],
        *,
        service: str,
        op: str,
        connector_id: int | None = None,
        method: str = "GET",
        **overrides: Any,
    ) -> Any:
        """执行外部调用：熔断检查 + 重试 + 可观测日志。失败抛 ``ExternalError``。"""
        circuit_key: str | None = None
        if self._breaker is not None:
            circuit_key = self.key(service=service, connector_id=connector_id, op=op)
        started = time.monotonic()
        last_attempt: dict = {"n": 0}

        def _on_attempt(n: int) -> None:
            last_attempt["n"] = n

        try:
            result = call_with_retry(
                fn,
                method=method,
                circuit_key=circuit_key,
                breaker=self._breaker,
                on_attempt=_on_attempt,
                **overrides,
            )
        except ExternalError as exc:
            log_external_call(
                {
                    "service": service,
                    "connector_id": connector_id,
                    "op": op,
                    "method": method,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "error_category": exc.kind.value,
                    "attempt": exc.attempts or last_attempt["n"],
                }
            )
            raise
        log_external_call(
            {
                "service": service,
                "connector_id": connector_id,
                "op": op,
                "method": method,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "error_category": None,
                "attempt": last_attempt["n"],
            }
        )
        return result

    async def acall(
        self,
        fn: Callable[[], Any],
        *,
        service: str,
        op: str,
        connector_id: int | None = None,
        method: str = "GET",
        **overrides: Any,
    ) -> Any:
        """async 版 call：供 httpx.AsyncClient 等异步出站调用使用（同一熔断键 + 可观测日志）。"""
        circuit_key: str | None = None
        if self._breaker is not None:
            circuit_key = self.key(service=service, connector_id=connector_id, op=op)
        started = time.monotonic()
        last_attempt: dict = {"n": 0}

        def _on_attempt(n: int) -> None:
            last_attempt["n"] = n

        try:
            result = await acall_with_retry(
                fn,
                method=method,
                circuit_key=circuit_key,
                breaker=self._breaker,
                on_attempt=_on_attempt,
                **overrides,
            )
        except ExternalError as exc:
            log_external_call(
                {
                    "service": service,
                    "connector_id": connector_id,
                    "op": op,
                    "method": method,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "error_category": exc.kind.value,
                    "attempt": exc.attempts or last_attempt["n"],
                }
            )
            raise
        log_external_call(
            {
                "service": service,
                "connector_id": connector_id,
                "op": op,
                "method": method,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "error_category": None,
                "attempt": last_attempt["n"],
            }
        )
        return result


external_resilience = ExternalResilience()
