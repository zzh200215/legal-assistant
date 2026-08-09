"""SQLAlchemy 轻量数据库监控：慢 SQL / 执行耗时 / 连接池 / 事务计数。

复用项目已有的 logging 与 observability 机制，不引入新监控框架。

安全约束：
- 默认不记录 SQL 参数（SQLAlchemy 参数化语句本身不含明文值，且我们从不打印 parameters），
  语句仅保留前 200 字符前缀，禁止泄露合同全文、Token、密码等敏感信息。
- 所有监听器异常都被吞掉：监控失败绝不影响主业务请求。
- 通过 contextvars 关联 task_id/request_id（见 set_db_correlation_id），
  慢 SQL 日志自动带上该关联 id。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_STATEMENT_PREFIX_MAX = 200

_correlation_id: ContextVar[str | None] = ContextVar("db_correlation_id", default=None)


def set_db_correlation_id(value: str | None) -> None:
    """设置当前异步任务/请求的关联 ID（task_id / request_id），慢 SQL 日志会带上。"""
    _correlation_id.set(value)


def get_db_correlation_id() -> str | None:
    return _correlation_id.get()


@dataclass
class DBMonitor:
    """进程内数据库运行指标（非持久化）。

    slow_query_ms 阈值每次读取实时配置，测试 patch 配置后立即生效。
    """

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _installed_engine_id: int | None = None
    query_count: int = 0
    slow_query_count: int = 0
    commit_count: int = 0
    rollback_count: int = 0
    error_count: int = 0
    checkout_count: int = 0
    checkin_count: int = 0
    total_query_ms: float = 0.0
    max_query_ms: float = 0.0
    pool_idle_ms_total: float = 0.0
    pool_idle_ms_max: float = 0.0
    slow_queries: deque = field(default_factory=lambda: deque(maxlen=20))

    def reset(self) -> None:
        with self._lock:
            self.query_count = 0
            self.slow_query_count = 0
            self.commit_count = 0
            self.rollback_count = 0
            self.error_count = 0
            self.checkout_count = 0
            self.checkin_count = 0
            self.total_query_ms = 0.0
            self.max_query_ms = 0.0
            self.pool_idle_ms_total = 0.0
            self.pool_idle_ms_max = 0.0
            self.slow_queries.clear()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "query_count": self.query_count,
                "slow_query_count": self.slow_query_count,
                "commit_count": self.commit_count,
                "rollback_count": self.rollback_count,
                "error_count": self.error_count,
                "checkout_count": self.checkout_count,
                "checkin_count": self.checkin_count,
                "total_query_ms": round(self.total_query_ms, 3),
                "avg_query_ms": round(self.total_query_ms / self.query_count, 3) if self.query_count else 0.0,
                "max_query_ms": round(self.max_query_ms, 3),
                "pool_idle_ms_avg": round(self.pool_idle_ms_total / self.checkout_count, 3) if self.checkout_count else 0.0,
                "pool_idle_ms_max": round(self.pool_idle_ms_max, 3),
                "recent_slow_queries": list(self.slow_queries),
            }

    # ---- 记录方法（内部调用，均持有锁） ----

    def _record_query(self, duration_ms: float, statement: str) -> None:
        with self._lock:
            self.query_count += 1
            self.total_query_ms += duration_ms
            if duration_ms > self.max_query_ms:
                self.max_query_ms = duration_ms
            threshold = self._slow_threshold_ms()
            if threshold > 0 and duration_ms >= threshold:
                self.slow_query_count += 1
                self.slow_queries.append(
                    {
                        "statement": statement[: _STATEMENT_PREFIX_MAX],
                        "duration_ms": round(duration_ms, 1),
                        "correlation_id": get_db_correlation_id(),
                        "logged_at": datetime.now(timezone.utc).isoformat(),
                    }
                )

    def _record_commit(self) -> None:
        with self._lock:
            self.commit_count += 1

    def _record_rollback(self) -> None:
        with self._lock:
            self.rollback_count += 1

    def _record_error(self) -> None:
        with self._lock:
            self.error_count += 1

    def _record_checkout(self, idle_ms: float | None) -> None:
        with self._lock:
            self.checkout_count += 1
            if idle_ms is not None:
                self.pool_idle_ms_total += idle_ms
                if idle_ms > self.pool_idle_ms_max:
                    self.pool_idle_ms_max = idle_ms

    def _record_checkin(self) -> None:
        with self._lock:
            self.checkin_count += 1

    def _slow_threshold_ms(self) -> int:
        try:
            from app.core.config import get_settings

            return int(get_settings().DATABASE_SLOW_QUERY_MS or 0)
        except Exception:  # noqa: BLE001 - 配置异常不阻断监控
            return 0


db_monitor = DBMonitor()


def pool_status(engine: "Engine") -> dict:
    """连接池即时使用情况（MySQL 池有效；SQLite 无池概念返回空）。"""
    try:
        status = engine.pool.status()
        return {
            "pool_size": status.pool_size,
            "checkedout_connections": status.checkedout_connections,
            "overflow": status.overflow,
        }
    except Exception:  # noqa: BLE001 - SQLite / 无池引擎
        return {}


def install_db_monitor(engine: "Engine", monitor: DBMonitor | None = None) -> DBMonitor:
    """为引擎挂载监控事件监听（幂等：同一引擎只挂载一次）。

    返回实际使用的 monitor 实例。
    """
    monitor = monitor or db_monitor
    if monitor._installed_engine_id is not None:
        if monitor._installed_engine_id == id(engine):
            return monitor
        raise RuntimeError("DBMonitor 已挂载到其他引擎实例")
    monitor._installed_engine_id = id(engine)

    from sqlalchemy import event

    @event.listens_for(engine, "before_cursor_execute")
    def _before_execute(conn, cursor, statement, parameters, context, executemany):
        try:
            conn.info["_db_monitor_start"] = time.perf_counter()
        except Exception:  # noqa: BLE001
            pass

    @event.listens_for(engine, "after_cursor_execute")
    def _after_execute(conn, cursor, statement, parameters, context, executemany):
        start = conn.info.pop("_db_monitor_start", None)
        if start is None:
            return
        try:
            monitor._record_query((time.perf_counter() - start) * 1000.0, statement)
        except Exception:  # noqa: BLE001
            pass

    @event.listens_for(engine, "handle_error")
    def _on_error(context):
        try:
            monitor._record_error()
        except Exception:  # noqa: BLE001
            pass

    @event.listens_for(engine, "commit")
    def _on_commit(conn):
        try:
            monitor._record_commit()
        except Exception:  # noqa: BLE001
            pass

    @event.listens_for(engine, "rollback")
    def _on_rollback(conn):
        try:
            monitor._record_rollback()
        except Exception:  # noqa: BLE001
            pass

    # 连接池：checkout/checkin 计数 + 连接池内闲置时长（近似表示池压力/等待）。
    # SQLAlchemy 不暴露线程等待 checkout 的直接钩子，用 idle 时长作为反向代理：
    # idle 越小越接近池容量耗尽。QueuePool 之外（SQLite/NullPool）不挂池监听。
    from sqlalchemy.pool import QueuePool

    if isinstance(engine.pool, QueuePool):
        @event.listens_for(engine.pool, "checkout")
        def _on_checkout(dbapi_connection, connection_record, connection_proxy):
            try:
                idle = connection_record.info.pop("_db_monitor_checked_in_at", None)
                if idle is not None:
                    monitor._record_checkout((time.perf_counter() - idle) * 1000.0)
                else:
                    monitor._record_checkout(None)
            except Exception:  # noqa: BLE001
                pass

        @event.listens_for(engine.pool, "checkin")
        def _on_checkin(dbapi_connection, connection_record):
            try:
                connection_record.info["_db_monitor_checked_in_at"] = time.perf_counter()
                monitor._record_checkin()
            except Exception:  # noqa: BLE001
                pass

    return monitor
