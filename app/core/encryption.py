"""AES-256-GCM encryption for sensitive database text fields."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings

_PREFIX = "enc:"


def _decode_key(configured: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(configured.encode("ascii"))
    except Exception as exc:
        raise ValueError("LEGAL_DATA_ENCRYPTION_KEY 必须是 32 字节 URL-safe Base64 密钥") from exc
    if len(decoded) != 32:
        raise ValueError("LEGAL_DATA_ENCRYPTION_KEY 必须解码为 32 字节")
    return decoded


def _key(version: str | None = None) -> tuple[str, bytes]:
    settings = get_settings()
    configured = settings.LEGAL_DATA_ENCRYPTION_KEY.strip()
    if settings.LEGAL_DATA_ENCRYPTION_KEYS_JSON:
        keys = __import__("json").loads(settings.LEGAL_DATA_ENCRYPTION_KEYS_JSON)
        selected = version or settings.LEGAL_DATA_ENCRYPTION_ACTIVE_VERSION
        if selected not in keys:
            raise ValueError("请求的法律数据加密密钥版本不可用")
        return selected, _decode_key(str(keys[selected]))
    if configured:
        return "v1", _decode_key(configured)
    raise RuntimeError(
        "缺少LEGAL_DATA_ENCRYPTION_KEY：法律数据加密不允许从SECRET_KEY派生。"
        "请在.env中配置独立的32字节URL-safe Base64密钥"
        "（如 python -c \"import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\"）"
    )


def encrypt_text(value: str) -> str:
    nonce = os.urandom(12)
    version, key = _key()
    ciphertext = AESGCM(key).encrypt(nonce, value.encode("utf-8"), None)
    return f"{_PREFIX}{version}:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_text(value: str) -> str:
    if not value.startswith(_PREFIX):
        return value  # Existing rows are migrated lazily when rewritten.
    _, version, encoded = value.split(":", 2)
    raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
    if len(raw) <= 12:
        raise ValueError("加密字段格式无效")
    return AESGCM(_key(version)[1]).decrypt(raw[:12], raw[12:], None).decode("utf-8")


class EncryptedText(TypeDecorator):
    """Stores UTF-8 text as AES-256-GCM ciphertext while exposing plaintext to ORM code."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        text = str(value)
        return text if text.startswith(_PREFIX) else encrypt_text(text)

    def process_result_value(self, value, dialect):
        return decrypt_text(value) if value is not None else None
