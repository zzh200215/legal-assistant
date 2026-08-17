"""存储抽象基类（无任何后端依赖，供 storage_service 与 cloud adapters 共同引用）。

单独成模块以打破 ``storage_service ↔ storage_cloud_adapters`` 的循环依赖：
- ``StorageAdapter``：统一流式接口（Protocol）；
- ``StorageBackendUnavailable``：所选后端不可用（SDK 未安装/配置缺失）。
"""

from __future__ import annotations

from typing import BinaryIO, Protocol


class StorageBackendUnavailable(RuntimeError):
    """所选存储后端不可用（SDK 未安装或配置缺失）。"""


class StorageAdapter(Protocol):
    """统一存储后端接口（流式）。"""

    provider: str

    def put_stream(self, key: str, source: BinaryIO, *, content_type: str | None = None) -> dict:
        """流式写入对象，返回 ``{"size": int, "content_hash": str}``。"""
        ...

    def get_stream(self, key: str) -> BinaryIO:
        """返回可读二进制流（调用方负责关闭）。"""
        ...

    def delete(self, key: str) -> None:
        ...

    def exists(self, key: str) -> bool:
        ...

    def get_metadata(self, key: str) -> dict:
        ...

    def generate_presigned_url(self, key: str, expires_in: int = 900) -> str:
        raise NotImplementedError
