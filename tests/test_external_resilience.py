"""外部调用韧性层测试：错误分类 / 退避重试 / 熔断 / 写超时不盲目重试。"""
import unittest
from unittest.mock import patch

import httpx

from app.core.circuit_breaker import CircuitBreaker
from app.core.external_resilience import (
    ExternalError,
    ExternalErrorKind,
    ExternalResilience,
    acall_with_retry,
    call_with_retry,
    classify_exception,
    classify_http_error,
    compute_backoff_delay,
)


def _http_error(status: int, body: str = "{}") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/x")
    response = httpx.Response(status, request=request, text=body)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class ErrorClassificationTests(unittest.TestCase):
    def test_retryable_kinds(self):
        for status in (429, 500, 502, 503):
            err = classify_http_error(_http_error(status))
            self.assertTrue(err.retryable, f"{status} 应可重试")
            self.assertEqual(err.status_code, status)
        # 网络/连接异常也可重试
        for exc in (httpx.ConnectError("boom"), httpx.ReadTimeout("boom")):
            err = classify_exception(exc)
            self.assertIn(err, (ExternalErrorKind.CONNECTION, ExternalErrorKind.TIMEOUT))
            self.assertTrue(ExternalError(kind=err, message="x").retryable)

    def test_non_retryable_kinds(self):
        for status in (400, 401, 403, 404, 422):
            err = classify_http_error(_http_error(status))
            self.assertFalse(err.retryable, f"{status} 不可重试")
        # 4xx 不计入熔断
        self.assertFalse(classify_http_error(_http_error(401)).counts_toward_breaker)

    def test_5xx_counts_toward_breaker_rate_limit_does_not(self):
        self.assertTrue(classify_http_error(_http_error(500)).counts_toward_breaker)
        self.assertFalse(classify_http_error(_http_error(429)).counts_toward_breaker,
                         "限流说明服务在线，不计入熔断")

    def test_write_timeout_is_ambiguous_side_effect(self):
        """外部写（POST）超时 → AMBIGUOUS_SIDE_EFFECT，不可重试。"""
        err = classify_http_error(httpx.ReadTimeout("write timed out"), method="POST")
        self.assertEqual(err.kind, ExternalErrorKind.AMBIGUOUS_SIDE_EFFECT)
        self.assertFalse(err.retryable)
        # GET 超时仍可重试
        err_get = classify_http_error(httpx.ReadTimeout("read timed out"), method="GET")
        self.assertEqual(err_get.kind, ExternalErrorKind.TIMEOUT)
        self.assertTrue(err_get.retryable)


class BackoffTests(unittest.TestCase):
    def test_exponential_backoff(self):
        delays = [compute_backoff_delay(i, base_seconds=2.0, jitter=False, max_wait_seconds=30)
                  for i in range(1, 5)]
        self.assertEqual(delays, [2.0, 4.0, 8.0, 16.0])

    def test_backoff_caps_at_max_wait(self):
        d = compute_backoff_delay(6, base_seconds=2.0, jitter=False, max_wait_seconds=30)
        self.assertEqual(d, 30.0)

    def test_respects_retry_after(self):
        d = compute_backoff_delay(1, base_seconds=2.0, jitter=False, max_wait_seconds=60,
                                  retry_after_seconds=7.0)
        self.assertEqual(d, 7.0)
        d_capped = compute_backoff_delay(1, base_seconds=2.0, jitter=False, max_wait_seconds=5,
                                         retry_after_seconds=7.0)
        self.assertEqual(d_capped, 5.0)

    def test_jitter_bounds(self):
        d = compute_backoff_delay(3, base_seconds=2.0, jitter=True, max_wait_seconds=30)
        self.assertGreaterEqual(d, 4.0)
        self.assertLessEqual(d, 8.0)


class RetryTests(unittest.TestCase):
    def _fast_kwargs(self):
        return {
            "max_attempts": 3,
            "max_wait_seconds": 0.01,
            "backoff_base_seconds": 0.01,
            "jitter": False,
        }

    def test_retries_5xx_then_succeeds(self):
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _http_error(503)
            return "ok"

        result = call_with_retry(_fn, method="GET", **self._fast_kwargs())
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3)

    def test_exhausts_attempts_raises(self):
        def _fn():
            raise _http_error(503)

        with self.assertRaises(ExternalError) as ctx:
            call_with_retry(_fn, method="GET", **self._fast_kwargs())
        self.assertEqual(ctx.exception.kind, ExternalErrorKind.SERVER_5XX)
        self.assertEqual(ctx.exception.attempts, 3)

    def test_non_retryable_no_retry(self):
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            raise _http_error(401)

        with self.assertRaises(ExternalError) as ctx:
            call_with_retry(_fn, method="GET", **self._fast_kwargs())
        self.assertEqual(ctx.exception.kind, ExternalErrorKind.AUTH)
        self.assertEqual(calls["n"], 1, "401 不可重试，只调用一次")

    def test_write_timeout_no_blind_retry(self):
        """POST 超时 → attempts==1 即抛 AMBIGUOUS，绝不盲目重试。"""
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            raise httpx.ReadTimeout("post timed out")

        with self.assertRaises(ExternalError) as ctx:
            call_with_retry(_fn, method="POST", **self._fast_kwargs())
        self.assertEqual(ctx.exception.kind, ExternalErrorKind.AMBIGUOUS_SIDE_EFFECT)
        self.assertEqual(calls["n"], 1, "写超时只尝试一次")

    def test_breaker_opens_then_half_open_recovers(self):
        clock = [1000.0]
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10,
                                 half_open_max_concurrency=1, now=lambda: clock[0])
        circuit_key = "external:svc|-|op"

        def _fail():
            raise _http_error(500)

        for _ in range(2):
            with self.assertRaises(ExternalError):
                call_with_retry(_fail, method="GET", circuit_key=circuit_key,
                                breaker=breaker, **self._fast_kwargs())
        self.assertEqual(breaker.state(circuit_key), "open")
        # open 期间快速失败
        with self.assertRaises(ExternalError) as ctx:
            call_with_retry(_fail, method="GET", circuit_key=circuit_key,
                            breaker=breaker, **self._fast_kwargs())
        self.assertEqual(ctx.exception.kind, ExternalErrorKind.CIRCUIT_OPEN)
        # 冷却结束 → half_open 探测成功 → closed
        clock[0] += 11
        result = call_with_retry(lambda: "ok", method="GET", circuit_key=circuit_key,
                                 breaker=breaker, **self._fast_kwargs())
        self.assertEqual(result, "ok")
        self.assertEqual(breaker.state(circuit_key), "closed")

    def test_breaker_half_open_probe_failure_reopens(self):
        clock = [1000.0]
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10,
                                 half_open_max_concurrency=1, now=lambda: clock[0])
        circuit_key = "external:svc|-|op"

        def _fail():
            raise _http_error(500)

        for _ in range(2):
            with self.assertRaises(ExternalError):
                call_with_retry(_fail, method="GET", circuit_key=circuit_key,
                                breaker=breaker, **self._fast_kwargs())
        clock[0] += 11
        with self.assertRaises(ExternalError):
            call_with_retry(_fail, method="GET", circuit_key=circuit_key,
                            breaker=breaker, **self._fast_kwargs())
        self.assertEqual(breaker.state(circuit_key), "open", "半开探测失败重新打开")

    def test_external_resilience_call_logs_and_raises(self):
        service = "smtp"
        with patch("app.core.external_resilience.log_external_call") as log:

            def _fn():
                raise _http_error(502)

            with self.assertRaises(ExternalError):
                ExternalResilience(breaker=None).call(
                    _fn, service=service, op="send", method="POST", **self._fast_kwargs())
            log.assert_called_once()
            record = log.call_args.args[0]
            self.assertEqual(record["service"], service)
            self.assertEqual(record["error_category"], "server_5xx")


class AsyncResilienceTests(unittest.TestCase):
    def test_async_write_timeout_no_retry(self):
        calls = {"n": 0}

        async def _fn():
            calls["n"] += 1
            raise httpx.ReadTimeout("async post timeout")

        async def _run():
            return await acall_with_retry(
                _fn, method="POST", max_attempts=3, max_wait_seconds=0.01,
                backoff_base_seconds=0.01, jitter=False,
            )

        import asyncio

        with self.assertRaises(ExternalError) as ctx:
            asyncio.run(_run())
        self.assertEqual(ctx.exception.kind, ExternalErrorKind.AMBIGUOUS_SIDE_EFFECT)
        self.assertEqual(calls["n"], 1)

    def test_async_retries_transient(self):
        calls = {"n": 0}

        async def _fn():
            calls["n"] += 1
            if calls["n"] < 2:
                raise _http_error(503)
            return "ok"

        async def _run():
            return await acall_with_retry(
                _fn, method="GET", max_attempts=3, max_wait_seconds=0.01,
                backoff_base_seconds=0.01, jitter=False,
            )

        import asyncio

        result = asyncio.run(_run())
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
