"""统一入站 Webhook 验签器（P1-C）。

所有第三方回调（签署 / Stripe 支付 / 飞书事件等）必须经 ``WebhookVerifier``
验证，禁止各路由自行拼接验签逻辑：

- 签名基于**原始请求体**（不重新序列化 JSON），HMAC-SHA256 + 常量时间比较。
- **时间戳新鲜度窗口**可配置；过期拒绝（``EXPIRED``）。
- 未配置密钥即拒绝（``NOT_CONFIGURED``，fail-closed），不静默放行。
- 验签错误消息**不包含密钥**；nonce 去重由调用方配合持久化存储完成
  （``app/core/webhook_dedup.claim_nonce``，见 docs/webhook-security.md）。

支持 scheme：

- ``raw``        : HMAC-SHA256(secret, raw_body)，encoding 支持 hex | base64
                   （签署回调：hex）
- ``stripe``     : 签名头 ``t=<ts>,v1=<hmac>``，签名串 ``f"{ts}." + body``，
                   hex 输出（与 Stripe 官方格式兼容）
- ``feishu_v2``  : base64(HMAC-SHA256(secret, f"{ts}{nonce}{secret}" + body))，
                   要求 ts + nonce
- ``feishu_v1``  : base64(HMAC-SHA256(secret, raw_body))
- ``feishu_auto``: 依次尝试 v2（ts+nonce 齐）→ v1 → 旧 hex（allow_legacy_hex）
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time as _time
from dataclasses import dataclass

from app.core.config import get_settings


class WebhookVerificationError(ValueError):
    """Webhook 验签失败（稳定错误码，消息不含密钥/载荷）。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class WebhookVerification:
    """验签通过后的结果（含已验证时间戳/非ce，供审计与去重使用）。"""

    timestamp: str | None
    nonce: str | None


class WebhookVerifier:
    """统一验签器。构造参数：

    - ``secret``：共享密钥（为空即 fail-closed 拒绝，不静默放行）。
    - ``scheme``：raw | stripe | feishu_v1 | feishu_v2 | feishu_auto。
    - ``encoding``：raw 方案的签名编码（hex | base64）。
    - ``tolerance_seconds``：时间戳窗口；None 时取
      ``WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS``（默认 300）。
    - ``allow_legacy_hex``：feishu_auto 是否兼容旧 hex 签名（默认 True，
      新接入环境勿依赖）。
    """

    def __init__(
        self,
        secret: str,
        *,
        scheme: str = "raw",
        encoding: str = "hex",
        tolerance_seconds: int | None = None,
        allow_legacy_hex: bool = True,
    ) -> None:
        if scheme not in {"raw", "stripe", "feishu_v1", "feishu_v2", "feishu_auto"}:
            raise ValueError(f"不支持的 scheme: {scheme}")
        if encoding not in {"hex", "base64"}:
            raise ValueError(f"不支持的 encoding: {encoding}")
        self._secret = secret or ""
        self._scheme = scheme
        self._encoding = encoding
        self._tolerance_seconds = tolerance_seconds
        self._allow_legacy_hex = allow_legacy_hex

    # ── 对外入口 ─────────────────────────────────────────────────

    def verify(
        self,
        raw_body: bytes,
        signature: str | None,
        *,
        timestamp: str | None = None,
        nonce: str | None = None,
    ) -> WebhookVerification:
        """验证原始请求体签名；通过返回 WebhookVerification，失败抛错误。

        ``timestamp``/``nonce`` 为请求头中的时间戳与 nonce（stripe 从签名头内
        解析，无需外部传入）。
        """
        if not self._secret:
            raise WebhookVerificationError(
                "NOT_CONFIGURED", "Webhook 验签密钥未配置，拒绝事件（fail-closed）"
            )
        ts, sig_material = self._extract_scheme_parts(signature, timestamp=timestamp, nonce=nonce)
        candidates = self._expected_candidates(raw_body, ts, nonce)
        if not any(hmac.compare_digest(expected, sig_material) for expected in candidates):
            raise WebhookVerificationError("INVALID_SIGNATURE", "Webhook 签名无效")
        self._check_freshness(ts)
        return WebhookVerification(timestamp=ts, nonce=nonce)

    # ── scheme 特有解析 ──────────────────────────────────────────

    def _extract_scheme_parts(
        self, signature: str | None, *, timestamp: str | None, nonce: str | None
    ) -> tuple[str | None, str]:
        if not signature:
            raise WebhookVerificationError("MISSING_FIELD", "缺少验签头")
        if self._scheme == "stripe":
            ts, provided = self._parse_stripe_header(signature)
            return ts, provided
        if self._scheme == "feishu_v2" and (not timestamp or not nonce):
            raise WebhookVerificationError("MISSING_FIELD", "飞书 v2 验签需要时间戳与 nonce")
        return timestamp, signature

    @staticmethod
    def _parse_stripe_header(signature: str) -> tuple[str, str]:
        """Stripe 签名字符串 ``t=<ts>,v1=<hmac>``。"""
        parts = dict(p.split("=", 1) for p in signature.split(",") if "=" in p)
        ts = parts.get("t", "")
        provided = parts.get("v1", "")
        if not ts or not provided:
            raise WebhookVerificationError("MISSING_FIELD", "签名头格式不正确")
        return ts, provided

    # ── 签名计算 ─────────────────────────────────────────────────

    def _expected_candidates(self, raw_body: bytes, ts: str | None, nonce: str | None) -> list[str]:
        """按 scheme 计算期望签名候选（feishu_auto 按 v2→v1→旧 hex 顺序）。"""
        key = self._secret.encode("utf-8")
        scheme = self._scheme
        if scheme == "stripe":
            if not ts:
                raise WebhookVerificationError("MISSING_FIELD", "签名缺少时间戳")
            return [hmac.new(key, f"{ts}.{raw_body.decode('utf-8')}".encode("utf-8"), hashlib.sha256).hexdigest()]
        if scheme == "feishu_v2":
            if not ts or not nonce:
                raise WebhookVerificationError("MISSING_FIELD", "飞书 v2 验签需要时间戳与 nonce")
            return [_feishu_v2_signature(key, self._secret, ts, nonce, raw_body)]
        if scheme == "feishu_v1":
            return [_feishu_v1_signature(key, raw_body)]
        if scheme == "feishu_auto":
            candidates: list[str] = []
            if ts and nonce:
                candidates.append(_feishu_v2_signature(key, self._secret, ts, nonce, raw_body))
            candidates.append(_feishu_v1_signature(key, raw_body))
            if self._allow_legacy_hex:
                # 兼容早期简化实现（hex 直签），新接入环境勿依赖
                candidates.append(hmac.new(key, raw_body, hashlib.sha256).hexdigest())
            return candidates
        # raw
        digest = hmac.new(key, raw_body, hashlib.sha256).digest()
        return [digest.hex() if self._encoding == "hex" else base64.b64encode(digest).decode("ascii")]

    # ── 时间戳新鲜度 ─────────────────────────────────────────────

    def _check_freshness(self, ts: str | None) -> None:
        """校验时间戳窗口；无时间戳的方案（raw/feishu_v1/旧 hex）跳过。"""
        if ts is None:
            return
        try:
            ts_int = int(ts)
        except ValueError:
            raise WebhookVerificationError("EXPIRED", "Webhook 签名时间戳非法") from None
        tolerance = self._tolerance_seconds or get_settings().WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS
        if abs(int(_time.time()) - ts_int) > tolerance:
            raise WebhookVerificationError("EXPIRED", "Webhook 签名时间戳已过期")


def _feishu_v1_signature(key: bytes, raw_body: bytes) -> str:
    return base64.b64encode(hmac.new(key, raw_body, hashlib.sha256).digest()).decode("ascii")


def _feishu_v2_signature(key: bytes, secret: str, ts: str, nonce: str, raw_body: bytes) -> str:
    v2_input = f"{ts}{nonce}{secret}".encode("utf-8") + raw_body
    return base64.b64encode(hmac.new(key, v2_input, hashlib.sha256).digest()).decode("ascii")