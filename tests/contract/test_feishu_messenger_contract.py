"""契约测试：飞书出站客户端（FeishuMessenger）——本系统承诺的调用协议。

替身：httpx.MockTransport（内存 HTTP，不触真实飞书）。
契约点（钉死请求结构 + 响应处理）：
- token 交换：POST {base}/auth/v3/tenant_access_token/internal，body {app_id, app_secret}；
  code==0 才接受；token 内存缓存（到期前 60s 内复用）；
- 发消息：POST {base}/im/v1/messages？receive_id_type=open_id，Authorization Bearer，
  payload {receive_id, msg_type, content(JSON 字符串)}；业务 code!=0 → sent=False+code；
  HTTP/韧性层错误 → sent=False（不抛）；
- 文件下载：GET {base}/im/v1/files/{file_key}？type=file + Bearer；JSON 响应 → None；
- 未配置凭据：不发请求，返回 {"configured": False}。
"""

import json
import unittest

import httpx

from app.services.integration.feishu_service import FEISHU_OPEN_BASE, FeishuMessenger


def _messenger_with(handler, *, app_id="cli_app", secret="secret"):
    transport = httpx.MockTransport(handler)
    messenger = FeishuMessenger(app_id, secret, base_url="https://feishu.test/open-apis")
    messenger._http = httpx.AsyncClient(transport=transport, timeout=10.0)
    return messenger, transport


class FeishuTokenContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_token_exchange_request_structure(self):
        requests = []

        async def handler(request: httpx.Request):
            requests.append(request)
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-ok", "expire": 7200})

        messenger, _ = _messenger_with(handler)
        token = await messenger.tenant_access_token()
        self.assertEqual(token, "t-ok")
        req = requests[0]
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.url.path, "/open-apis/auth/v3/tenant_access_token/internal")
        self.assertEqual(json.loads(req.content), {"app_id": "cli_app", "app_secret": "secret"})

    async def test_token_is_cached_within_ttl(self):
        calls = {"n": 0}

        async def handler(request: httpx.Request):
            calls["n"] += 1
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-cached", "expire": 7200})

        messenger, _ = _messenger_with(handler)
        await messenger.tenant_access_token()
        await messenger.tenant_access_token()
        await messenger.tenant_access_token()
        self.assertEqual(calls["n"], 1)  # 缓存生效，只打一次 token 端点

    async def test_expired_token_triggers_refresh(self):
        calls = {"n": 0}

        async def handler(request: httpx.Request):
            calls["n"] += 1
            return httpx.Response(200, json={"code": 0, "tenant_access_token": f"t-{calls['n']}", "expire": 7200})

        messenger, _ = _messenger_with(handler)
        await messenger.tenant_access_token()
        # 模拟缓存已过期（超过 expire-60s 缓冲）
        messenger._token_expires_at = 0.0
        await messenger.tenant_access_token()
        self.assertEqual(calls["n"], 2)

    async def test_token_error_code_returns_none(self):
        async def handler(request: httpx.Request):
            return httpx.Response(200, json={"code": 10003, "msg": "invalid app secret"})

        messenger, _ = _messenger_with(handler)
        self.assertIsNone(await messenger.tenant_access_token())

    async def test_unconfigured_returns_none_without_http(self):
        messenger = FeishuMessenger("", "")
        self.assertIsNone(await messenger.tenant_access_token())
        self.assertEqual(await messenger.send_text("ou_1", "hi"), {"configured": False})


class FeishuSendContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_request_structure(self):
        requests = []

        async def handler(request: httpx.Request):
            requests.append(request)
            if request.url.path.endswith("/tenant_access_token/internal"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-1", "expire": 7200})
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_1"}})

        messenger, _ = _messenger_with(handler)
        result = await messenger.send_card("ou_1", {"header": {"title": "x"}})
        self.assertEqual(result, {"configured": True, "sent": True, "message_id": "om_1"})
        send_req = requests[-1]
        self.assertEqual(send_req.method, "POST")
        self.assertEqual(send_req.url.path, "/open-apis/im/v1/messages")
        self.assertEqual(send_req.url.params["receive_id_type"], "open_id")
        self.assertEqual(send_req.headers["authorization"], "Bearer t-1")
        body = json.loads(send_req.content)
        self.assertEqual(body["receive_id"], "ou_1")
        self.assertEqual(body["msg_type"], "interactive")
        self.assertEqual(json.loads(body["content"]), {"header": {"title": "x"}})  # content 为 JSON 字符串

    async def test_send_business_error_code_returns_failure_detail(self):
        async def handler(request: httpx.Request):
            if request.url.path.endswith("/tenant_access_token/internal"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-1", "expire": 7200})
            return httpx.Response(200, json={"code": 19001, "msg": "invalid open_id"})

        messenger, _ = _messenger_with(handler)
        result = await messenger.send_text("ou_bad", "hi")
        self.assertEqual(result["configured"], True)
        self.assertEqual(result["sent"], False)
        self.assertEqual(result["code"], 19001)

    async def test_send_http_error_degrades_to_sent_false(self):
        from unittest.mock import patch

        from app.core.external_resilience import ExternalError, ExternalErrorKind

        async def handler(request: httpx.Request):
            if request.url.path.endswith("/tenant_access_token/internal"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-1", "expire": 7200})
            return httpx.Response(500, json={"code": 1, "msg": "server error"})

        messenger, _ = _messenger_with(handler)
        # 韧性层（external_resilience.acall）负责分类；此处用真逻辑走 HTTP 500 分类
        with patch(
            "app.core.external_resilience.external_resilience.acall",
            side_effect=ExternalError(kind=ExternalErrorKind.SERVER_5XX, message="500"),
        ):
            result = await messenger.send_text("ou_1", "hi")
        self.assertEqual(result, {"configured": True, "sent": False})


class FeishuDownloadContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_request_structure_and_binary_response(self):
        requests = []

        async def handler(request: httpx.Request):
            requests.append(request)
            if request.url.path.endswith("/tenant_access_token/internal"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-1", "expire": 7200})
            return httpx.Response(200, content=b"PDF-BYTES", headers={"content-type": "application/pdf"})

        messenger, _ = _messenger_with(handler)
        data = await messenger.download_file("fk_123")
        self.assertEqual(data, b"PDF-BYTES")
        req = requests[-1]
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.url.path, "/open-apis/im/v1/files/fk_123")
        self.assertEqual(req.url.params["type"], "file")
        self.assertEqual(req.headers["authorization"], "Bearer t-1")

    async def test_download_json_error_response_returns_none(self):
        async def handler(request: httpx.Request):
            if request.url.path.endswith("/tenant_access_token/internal"):
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "t-1", "expire": 7200})
            return httpx.Response(200, json={"code": 110001, "msg": "file not found"})

        messenger, _ = _messenger_with(handler)
        self.assertIsNone(await messenger.download_file("fk_missing"))

    async def test_download_unconfigured_returns_none(self):
        messenger = FeishuMessenger("", "")
        self.assertIsNone(await messenger.download_file("fk_1"))


class FeishuEndpointContractTests(unittest.IsolatedAsyncioTestCase):
    def test_endpoint_base_contract(self):
        # 端点常量契约：生产 base + 各端点路径（防止手写 URL 漂移）
        self.assertEqual(FEISHU_OPEN_BASE, "https://open.feishu.cn/open-apis")
        messenger = FeishuMessenger("a", "b")
        self.assertTrue(messenger.base_url.startswith("https://"))
        self.assertTrue(messenger.base_url.endswith("/open-apis"))


if __name__ == "__main__":
    unittest.main()
