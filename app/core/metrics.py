"""P1 统一 metrics facade：进程内线程安全计数 + 固定桶直方图 + 窗口快照。

设计约束（对应 P1 SLO 契约）：
- 非阻塞：任何异常仅记录日志，绝不导致业务失败；OBS_METRICS_SNAPSHOT_ENABLED
  关闭时所有调用接近零开销。
- 高基数字段禁止作为 label：request_id / trace_id / user_id / document_id /
  agent_run_id / task_id 等键在记录时被剥离（并告警），由调用方负责业务维度。
- 失败原因只允许有限枚举（error_category / status），禁止异常文本作 label。
- 快照由 beat 任务按 OBS_METRICS_SNAPSHOT_WINDOW_SECONDS 落库 ops_metric_snapshots，
  小时/天级 SLO 由预聚合任务从明细与快照幂等聚合。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 固定延迟桶（毫秒）：api_request_duration 直方图。
LATENCY_BUCKETS_MS = (50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000)

# 禁止作 label 的高基数字段（P1 硬约束，命中即剥离并告警）。
_HIGH_CARDINALITY_KEYS = frozenset(
    {
        "request_id", "trace_id", "span_id", "user_id", "document_id",
        "agent_run_id", "task_id", "connector_id", "notification_id",
        "email_id", "sync_run_id", "job_id", "run_id", "id",
    }
)

# 指标名常量（SLO/运营统一口径）。
METRIC_API_REQUEST_DURATION = "api_request_duration"
METRIC_LLM_CALLS = "llm_calls"
METRIC_LLM_LATENCY = "llm_latency_ms"
METRIC_DOC_PARSE = "doc_parse_jobs"
METRIC_AGENT_RUNS = "agent_runs"
METRIC_NOTIFICATION_DELIVERIES = "notification_deliveries"
METRIC_TASK_OUTCOMES = "task_outcomes"
METRIC_CONNECTOR_SYNCS = "connector_syncs"
METRIC_EMAIL_DELIVERIES = "email_deliveries"
METRIC_TASK_BACKLOG = "task_backlog"  # gauge：DB 口径（running/过期回收）
METRIC_BROKER_BACKLOG = "broker_backlog"  # gauge：Redis LLEN 按队列
METRIC_MODEL_COST = "model_cost"  # Decimal 金额，聚合层从 cost_ledger 计算

# 预聚合层使用的 SLO 指标名（与聚合任务契约一致）。
SLO_METRICS = frozenset(
    {
        METRIC_API_REQUEST_DURATION,
        METRIC_LLM_CALLS,
        METRIC_LLM_LATENCY,
        METRIC_DOC_PARSE,
        METRIC_AGENT_RUNS,
        METRIC_NOTIFICATION_DELIVERIES,
        METRIC_TASK_OUTCOMES,
        METRIC_CONNECTOR_SYNCS,
        METRIC_TASK_BACKLOG,
        METRIC_BROKER_BACKLOG,
        METRIC_MODEL_COST,
    }
)


def _enabled() -> bool:
    try:
        from app.core.config import get_settings

        return bool(get_settings().OBS_METRICS_SNAPSHOT_ENABLED)
    except Exception:  # noqa: BLE001 - 配置异常按关闭处理
        return False


def _norm_labels(labels: dict | None) -> tuple[tuple[str, str], ...]:
    """归一化并剥离高基数标签；label 值转 str 且按 key 排序（稳定唯一键）。"""
    if not labels:
        return ()
    cleaned: dict[str, str] = {}
    for key, value in labels.items():
        name = str(key)
        if name in _HIGH_CARDINALITY_KEYS:
            logger.warning("metrics: 高基数标签 %s 被剥离（禁止作为指标 label）", name)
            continue
        if value is None:
            continue
        cleaned[name] = str(value)
    return tuple(sorted(cleaned.items()))


class _Histogram:
    __slots__ = ("buckets", "counts", "count", "sum_ms")

    def __init__(self, buckets: tuple[float, ...] = LATENCY_BUCKETS_MS):
        self.buckets = buckets
        self.counts = [0] * (len(buckets) + 1)
        self.count = 0
        self.sum_ms = 0.0

    def observe(self, ms: float) -> None:
        self.count += 1
        self.sum_ms += ms
        for index, upper in enumerate(self.buckets):
            if ms <= upper:
                self.counts[index] += 1
                return
        self.counts[-1] += 1

    def p95(self) -> float | None:
        """桶内线性插值 p95；无数据返回 None。"""
        if not self.count:
            return None
        target = self.count * 0.95
        cumulative = 0
        lower = 0.0
        for index, upper in enumerate(self.buckets):
            cumulative += self.counts[index]
            if cumulative >= target:
                if self.counts[index]:
                    fraction = (target - (cumulative - self.counts[index])) / self.counts[index]
                    return round(lower + fraction * (upper - lower), 1)
                return float(upper)
            lower = float(upper)
        return float(self.buckets[-1])


class MetricsRegistry:
    """线程安全进程内注册表；窗口快照后计数/直方图清零（gauge 保留最新值）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], int] = {}
        self._histograms: dict[tuple[str, tuple], _Histogram] = {}
        self._gauges: dict[tuple[str, tuple], float] = {}

    # ── 记录入口（业务侧调用，全部非阻塞）────────────────────────────────────

    def increment(self, metric_name: str, labels: dict | None = None, value: int | float = 1) -> None:
        if not _enabled():
            return
        try:
            key = (metric_name, _norm_labels(labels))
            with self._lock:
                self._counters[key] = self._counters.get(key, 0) + int(value)
        except Exception:  # noqa: BLE001 - 指标采集失败不影响业务
            logger.warning("metrics.increment failed for %s", metric_name, exc_info=True)

    def observe(self, metric_name: str, duration_ms: float, labels: dict | None = None) -> None:
        if not _enabled():
            return
        try:
            key = (metric_name, _norm_labels(labels))
            with self._lock:
                histogram = self._histograms.get(key)
                if histogram is None:
                    histogram = self._histograms[key] = _Histogram()
                histogram.observe(max(float(duration_ms), 0.0))
        except Exception:  # noqa: BLE001
            logger.warning("metrics.observe failed for %s", metric_name, exc_info=True)

    def set_gauge(self, metric_name: str, value: float, labels: dict | None = None) -> None:
        if not _enabled():
            return
        try:
            key = (metric_name, _norm_labels(labels))
            with self._lock:
                self._gauges[key] = float(value)
        except Exception:  # noqa: BLE001
            logger.warning("metrics.set_gauge failed for %s", metric_name, exc_info=True)

    # ── 快照（beat 任务调用）──────────────────────────────────────────────────

    def snapshot_and_reset(self) -> list[dict[str, Any]]:
        """返回本窗口增量并清零计数/直方图；gauge 保留最新值（重复快照不丢）。"""
        with self._lock:
            items: list[dict[str, Any]] = []
            for (name, labels), count in self._counters.items():
                items.append({
                    "metric_name": name, "labels": dict(labels), "kind": "counter",
                    "count": count,
                })
            self._counters.clear()
            for (name, labels), histogram in self._histograms.items():
                items.append({
                    "metric_name": name, "labels": dict(labels), "kind": "histogram",
                    "count": histogram.count,
                    "sum_value": round(histogram.sum_ms, 3),
                    "p95_value": histogram.p95(),
                })
            self._histograms.clear()
            for (name, labels), value in self._gauges.items():
                items.append({
                    "metric_name": name, "labels": dict(labels), "kind": "gauge",
                    "count": value,
                })
            return items

    def enabled(self) -> bool:
        return _enabled()


metrics = MetricsRegistry()
