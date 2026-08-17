"""版本冲突契约工具（P1 API 统一化）：If-Match 解析与 ETag 生成。

约定：ETag 形如 ``"v{n}"``（资源 version 列），If-Match 接受 ``"v{n}"`` 或 ``v{n}``。
版本不匹配由 service 层抛 StaleDataError，全局 handler 映射 409 CONCURRENT_UPDATE_CONFLICT。
"""

from __future__ import annotations


def parse_if_match(value: str | None) -> int | None:
    """解析 If-Match: "v{n}" / v{n}；未提供或无法解析返回 None（视为未提供）。"""
    if not value:
        return None
    token = value.strip().strip('"').strip()
    if token.startswith("v"):
        token = token[1:]
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def etag_for(version: int | None) -> str:
    """由资源 version 列生成 ETag。"""
    return f'"v{int(version or 0)}"'
