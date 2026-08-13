"""Mock 连接器客户端与 sink：内存数据集 + 分页 + 可注入中断/故障点。

仅供演示与测试：不复活已删除的 IMAP/Graph 等入站连接器。
CONNECTOR_SYNC_ENABLED 默认关闭，仅在显式开启后由 ``connector_sync_task`` 使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SyncPage:
    items: list[dict]
    next_cursor: str | None
    source_version: str | None
    has_more: bool


class MockConnectorClient:
    """内存数据集分页客户端：cursor 为整数索引字符串。

    ``interrupt_after=N``：第 N 次 page 调用抛异常（模拟网络中断/超时）。
    """

    def __init__(self, items: list[dict], interrupt_after: int | None = None) -> None:
        self._items = list(items)
        self._interrupt_after = interrupt_after
        self.calls = 0

    def page(self, connector_id: int, cursor: str | None, page_size: int = 100) -> SyncPage:
        self.calls += 1
        if self._interrupt_after is not None and self.calls >= self._interrupt_after:
            raise RuntimeError("mock connector interrupted")
        start = int(cursor) if cursor else 0
        items = self._items[start:start + page_size]
        next_start = start + len(items)
        has_more = next_start < len(self._items)
        return SyncPage(
            items=items,
            next_cursor=str(next_start) if has_more else None,
            source_version=f"cursor-{next_start}",
            has_more=has_more,
        )


class MockSink:
    """本地落地 sink：记录已提交的外部对象（可计数、可注入失败）。"""

    def __init__(self, fail_external_id: str | None = None) -> None:
        self.seen: list[str] = []
        self.upserts = 0
        self._fail_external_id = fail_external_id

    def write(self, db: Any, connector_id: int, item: dict) -> None:
        if self._fail_external_id == item["external_id"]:
            raise RuntimeError("mock sink failed")
        self.seen.append(item["external_id"])
        self.upserts += 1


def build_mock_client(connector: Any, *, interrupt_after: int | None = None) -> MockConnectorClient:
    """按 connector 生成确定性样本数据集（演示/测试用）。"""
    seed = (int(connector.id) % 7) + 1
    items = [
        {"external_id": f"external-{connector.id}-{i}", "version": f"v{seed}-{i}", "data": {"n": i}}
        for i in range(seed)
    ]
    return MockConnectorClient(items=items, interrupt_after=interrupt_after)


def build_mock_sink(connector: Any) -> MockSink:
    return MockSink()
