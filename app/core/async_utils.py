"""同步/异步桥接：在任意上下文中安全地运行协程。"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Coroutine


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """运行协程并返回其结果。

    - 当前没有运行中的事件循环：直接 ``asyncio.run``；
    - 已在事件循环内（例如 async 请求处理器/异步 worker 调用了同步服务方法）：
      在独立线程中运行 ``asyncio.run``，避免抛出
      ``RuntimeError: asyncio.run() cannot be called from a running event loop``。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
