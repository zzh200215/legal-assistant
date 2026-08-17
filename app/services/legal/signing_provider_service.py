"""受控的法大大签署适配器。

凭据只从部署配置读取，业务 API 不能提交服务商流水号或密钥。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.core.config import get_settings


@dataclass(frozen=True)
class SigningDispatch:
    provider_request_id: str


class FadadaSigningProvider:
    name = "fadada"

    def _config(self) -> tuple[str, str]:
        settings = get_settings()
        endpoint = getattr(settings, "SIGNING_FADADA_SANDBOX_URL", "").strip()
        api_key = getattr(settings, "SIGNING_FADADA_API_KEY", "").strip()
        if not endpoint or not api_key:
            raise ValueError("组织尚未配置法大大沙箱服务")
        return endpoint.rstrip("/"), api_key

    def create_and_send(self, *, request_id: int, contract_version_id: int, parties: list[dict], deadline_at) -> SigningDispatch:
        endpoint, api_key = self._config()
        payload = json.dumps({
            "platform_request_id": str(request_id),
            "contract_version_id": str(contract_version_id),
            "parties": parties,
            "deadline_at": deadline_at.isoformat() if deadline_at else None,
        }).encode("utf-8")
        request = Request(
            f"{endpoint}/sign-requests", data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except (URLError, TimeoutError, ValueError) as exc:
            raise ValueError("法大大签署单创建失败，已保留草稿可重试") from exc
        provider_request_id = str(response_payload.get("provider_request_id") or response_payload.get("request_id") or "").strip()
        if not provider_request_id:
            raise ValueError("法大大响应缺少签署单号，已保留草稿可重试")
        return SigningDispatch(provider_request_id=provider_request_id)


signing_provider_service = FadadaSigningProvider()
