"""AES-256-GCM encryption for sensitive database text fields.

密钥经统一 SecretProvider 读取（P1-A 密钥管理）：默认 env 提供方，语义与历史
环境变量完全兼容（版本化密钥环 + 激活版本 + 单密钥降级 v1）；接入 KMS 时
无需修改本模块（见 app/core/secrets）。
"""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings
from app.core.secrets import SECRET_LEGAL_DATA_ENCRYPTION, SecretDecryptionError, get_secret_provider

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
    """按版本从统一 SecretProvider 取密钥；返回 (version, key_bytes)。

    - version 为空 → 使用当前激活版本；缺失配置抛 RuntimeError（同历史行为）。
    - 指定版本不在密钥环 → SecretNotFoundError（ValueError 子类，fail-closed）。
    - settings 显式注入，保持既有测试 patch(encryption.get_settings) 兼容。
    """
    settings = get_settings()
    provider = get_secret_provider(settings=settings)
    selected = version or provider.current_version(SECRET_LEGAL_DATA_ENCRYPTION)
    if selected is None:
        raise RuntimeError(
            "缺少LEGAL_DATA_ENCRYPTION_KEY：法律数据加密不允许从SECRET_KEY派生。"
            "请在.env中配置独立的32字节URL-safe Base64密钥"
            "（如 python -c \"import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\"）"
        )
    raw = provider.get_version(SECRET_LEGAL_DATA_ENCRYPTION, selected)
    return selected, _decode_key(raw)


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
    key = _key(version)[1]
    try:
        return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode("utf-8")
    except Exception as exc:
        # 密钥错误/密文被篡改/编码损坏：统一稳定错误，fail-closed，消息不含密钥。
        raise SecretDecryptionError("密文解密失败（密钥错误或密文被篡改）") from exc


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
