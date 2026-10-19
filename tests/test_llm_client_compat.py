import asyncio
import unittest

import httpx

from app.core.llm_client import (
    RATE_LIMIT_EXTRA_RETRIES,
    RETRY_AFTER_MAX_SECONDS,
    TRANSPORT_EXTRA_RETRIES,
    LLMClient,
    ModelGateway,
    _retry_after_seconds,
    _retry_delay_seconds,
    llm_client,
)
from app.core.ollama_client import OllamaClient, ollama_client


class LLMClientCompatibilityTests(unittest.TestCase):
    def test_backward_compatibility_aliases_point_to_new_client(self):
        self.assertIs(OllamaClient, LLMClient)
        self.assertIs(ollama_client, llm_client)


class ClientPoolEventLoopTests(unittest.TestCase):
    """连接池按事件循环隔离：临时循环（asyncio.run 的线程）不能污染主循环的池。

    回归背景：临时循环关闭后，池里的 keep-alive 连接被主循环复用时抛
    ``RuntimeError: Event loop is closed``，表现为「第二次咨询直接降级为规则兜底」。
    """

    def setUp(self):
        self.gateway = ModelGateway()
        self.target = self.gateway.primary_target
        self.addCleanup(self.gateway._clients.clear)

    def test_same_loop_reuses_client(self):
        async def main():
            first = self.gateway._get_client(self.target, timeout=5.0)
            second = self.gateway._get_client(self.target, timeout=5.0)
            return first, second

        first, second = asyncio.run(main())
        self.assertIs(first, second)

    def test_new_loop_gets_a_new_client(self):
        async def main():
            return self.gateway._get_client(self.target, timeout=5.0)

        first = asyncio.run(main())
        second = asyncio.run(main())
        self.assertIsNot(first, second)

    def test_close_skips_clients_from_closed_loops(self):
        async def main():
            return self.gateway._get_client(self.target, timeout=5.0)

        client = asyncio.run(main())
        # 临时循环已关闭：close 不能去 aclose 它（会再抛 Event loop is closed），只清空引用。
        asyncio.run(self.gateway.close())
        self.assertEqual(self.gateway._clients, {})
        self.assertFalse(client.is_closed)


class RetryAfterTests(unittest.TestCase):
    """429 的 Retry-After 要被尊重：免费额度按分钟窗口限流，固定退避等不到窗口。

    回归背景：整轮评测在 ``Client error '429 Too Many Requests'`` 上直接失败——
    `_post_json_with_retry` 的固定退避（1.5s/3s/4.5s）比供应商的限流窗口短得多。
    """

    @staticmethod
    def _http_error(retry_after: str | None, status: int = 429) -> httpx.HTTPStatusError:
        headers = {"retry-after": retry_after} if retry_after is not None else {}
        request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        response = httpx.Response(status, headers=headers, request=request)
        return httpx.HTTPStatusError("rate limited", request=request, response=response)

    def test_reads_numeric_retry_after(self):
        self.assertEqual(_retry_after_seconds(self._http_error("12")), 12.0)

    def test_ignores_http_date_and_garbage(self):
        self.assertIsNone(_retry_after_seconds(self._http_error("Wed, 21 Oct 2026 07:28:00 GMT")))
        self.assertIsNone(_retry_after_seconds(self._http_error("soon")))

    def test_caps_long_waits(self):
        self.assertEqual(_retry_after_seconds(self._http_error("600")), RETRY_AFTER_MAX_SECONDS)

    def test_missing_header_and_plain_exception(self):
        self.assertIsNone(_retry_after_seconds(self._http_error(None)))
        self.assertIsNone(_retry_after_seconds(RuntimeError("boom")))

    def test_delay_prefers_the_larger_of_backoff_and_retry_after(self):
        # 第一次尝试：固定退避 1.5s，但供应商要求 30s → 等 30s
        self.assertEqual(_retry_delay_seconds(self._http_error("30"), 1), 30.0)
        # 没有 Retry-After 时保持指数退避
        self.assertEqual(_retry_delay_seconds(self._http_error(None), 2), 3.0)

    def test_rate_limited_backs_off_exponentially_and_caps(self):
        # 429 没有 Retry-After 时也必须能跨过「分钟窗口」：1.5→3→6→12→24→30（封顶）
        delays = [_retry_delay_seconds(self._http_error(None), attempt) for attempt in range(1, 7)]
        self.assertEqual(delays, [1.5, 3.0, 6.0, 12.0, 24.0, 30.0])

    def test_other_transient_errors_keep_linear_backoff(self):
        self.assertEqual(_retry_delay_seconds(self._http_error(None, status=503), 3), 4.5)

    def test_transport_errors_back_off_exponentially_with_lower_cap(self):
        error = httpx.ConnectError("connection reset")
        delays = [_retry_delay_seconds(error, attempt) for attempt in range(1, 6)]
        self.assertEqual(delays, [1.5, 3.0, 6.0, 12.0, 15.0])

    def test_rate_limit_gets_extra_attempts(self):
        self.assertGreaterEqual(RATE_LIMIT_EXTRA_RETRIES, 3)

    def test_transport_gets_extra_attempts(self):
        self.assertGreaterEqual(TRANSPORT_EXTRA_RETRIES, 3)


if __name__ == "__main__":
    unittest.main()
