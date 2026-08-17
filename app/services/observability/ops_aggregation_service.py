"""P1 预聚合服务：按时间桶 + org + 有限枚举标签，从明细/快照幂等聚合 SLO 指标。

设计约束（对应 P1 要求）：
- 幂等：每个 (granularity, bucket_start, metric_name, org_id, labels) 行先删后插，
  重复执行/失败重试/按窗口重算都不会重复累加。
- 断点恢复：ops_metric_watermarks 记录每 (granularity, metric_name) 最后完成桶；
  任务从水位线 + 1 继续，可随时中断续跑。
- 查询用 SQL GROUP BY（不在 Python 内存聚合明细），聚合任务只扫本桶范围且带
  created_at 索引，运营统计 API 不再实时扫描全部明细。
- 金额一律 Numeric/Decimal（model_cost），禁止 float。
- 失败原因/状态等标签均为有限枚举；org_id 允许作为维度（租户隔离要求）。
- 数据缺失行为：某桶无数据则不产出该桶行（不填 0 假装）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.cost_ledger import CostLedgerEntry
from app.models.document import Document, DocumentParseJob
from app.models.agent import AgentRun
from app.models.legal_notifications import LegalNotificationEvent
from app.models.llm_call_log import LLMCallLog
from app.models.ops_metric import OpsMetricDaily, OpsMetricHourly, OpsMetricSnapshot, OpsMetricWatermark
from app.models.task_run import TaskRun
from app.models.token_usage import TokenUsage
from app.models.user import User

logger = logging.getLogger(__name__)

GRANULARITY_HOUR = "hour"
GRANULARITY_DAY = "day"

# 明细源指标（按源表聚合）。
METRIC_LLM_CALLS = "llm_calls"
METRIC_DOC_PARSE = "doc_parse_jobs"
METRIC_AGENT_RUNS = "agent_runs"
METRIC_NOTIFICATION_DELIVERIES = "notification_deliveries"
METRIC_TASK_OUTCOMES = "task_outcomes"
METRIC_MODEL_COST = "model_cost"
# 快照源指标（进程内 recorder 快照 → 小时/天级）。
METRIC_API_REQUEST_DURATION = "api_request_duration"
METRIC_TASK_BACKLOG = "task_backlog"
METRIC_BROKER_BACKLOG = "broker_backlog"

_SNAPSHOT_SOURCE_METRICS = frozenset(
    {METRIC_API_REQUEST_DURATION, METRIC_TASK_BACKLOG, METRIC_BROKER_BACKLOG}
)

# 通知投递分母/分子状态（统一口径）。
_NOTIFICATION_DENOMINATOR = ("sent", "delivered", "failed", "dead_letter")
_NOTIFICATION_NUMERATOR = ("sent", "delivered")

# 文档解析：已开始（started_at 非空）且达到终态。
_DOC_TERMINAL = ("succeeded", "failed")

# Agent 终态（cancelled 按配置排除）。
_AGENT_TERMINAL = ("completed", "error", "cancelled")

# 任务台账终态（retrying 非终态，不进入成功率分母）。
_TASK_TERMINAL = ("succeeded", "failed")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def floor_bucket(value: datetime, granularity: str) -> datetime:
    value = value.replace(tzinfo=None)
    if granularity == GRANULARITY_HOUR:
        return value.replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def next_bucket(value: datetime, granularity: str) -> datetime:
    if granularity == GRANULARITY_HOUR:
        return value + timedelta(hours=1)
    return value + timedelta(days=1)


def bucket_end(value: datetime, granularity: str) -> datetime:
    return next_bucket(value, granularity)


def _labels_json(labels: dict) -> str:
    return json.dumps({key: str(value) for key, value in sorted(labels.items())}, ensure_ascii=False, sort_keys=True)


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return None


# ── 明细源聚合（SQL GROUP BY，单桶范围）──────────────────────────────────────

def _rows_llm_calls(db: Session, start: datetime, end: datetime) -> list[dict]:
    status_expr = LLMCallLog.status
    numerator = func.sum(case((status_expr == "success", 1), else_=0))
    denominator = func.sum(case((status_expr != "blocked", 1), else_=0))
    q = (
        db.query(
            User.organization_id.label("org_id"),
            LLMCallLog.model_name.label("model"),
            LLMCallLog.status.label("status"),
            LLMCallLog.error_category.label("error_category"),
            func.count().label("count"),
            numerator.label("numerator"),
            denominator.label("denominator"),
        )
        .join(User, User.id == LLMCallLog.user_id)
        .filter(LLMCallLog.created_at >= start, LLMCallLog.created_at < end)
        .group_by(User.organization_id, LLMCallLog.model_name, LLMCallLog.status, LLMCallLog.error_category)
        .all()
    )
    return [
        {
            "metric_name": METRIC_LLM_CALLS,
            "org_id": row.org_id,
            "labels": {"model": row.model or "unknown", "status": row.status or "unknown",
                       "error_category": row.error_category or "none"},
            "count": row.count,
            "numerator": row.numerator,
            "denominator": row.denominator,
        }
        for row in q
    ]


def _rows_doc_parse(db: Session, start: datetime, end: datetime) -> list[dict]:
    q = (
        db.query(
            User.organization_id.label("org_id"),
            DocumentParseJob.job_type.label("job_type"),
            Document.file_type.label("file_type"),
            DocumentParseJob.status.label("status"),
            func.count().label("count"),
        )
        .join(Document, Document.id == DocumentParseJob.document_id)
        .join(User, User.id == DocumentParseJob.user_id)
        .filter(
            DocumentParseJob.started_at.isnot(None),
            DocumentParseJob.started_at >= start,
            DocumentParseJob.started_at < end,
            DocumentParseJob.status.in_(_DOC_TERMINAL),
        )
        .group_by(User.organization_id, DocumentParseJob.job_type, Document.file_type, DocumentParseJob.status)
        .all()
    )
    return [
        {
            "metric_name": METRIC_DOC_PARSE,
            "org_id": row.org_id,
            "labels": {"job_type": row.job_type or "unknown", "file_type": row.file_type or "unknown",
                       "status": row.status or "unknown"},
            "count": row.count,
            # 分子=成功解析数；分母=已开始且达终态数（本行即终态分组）。
            "numerator": row.count if row.status == "succeeded" else 0,
            "denominator": row.count,
        }
        for row in q
    ]


def _rows_agent_runs(db: Session, start: datetime, end: datetime) -> list[dict]:
    settings = get_settings()
    terminal = list(_AGENT_TERMINAL)
    if settings.OBS_AGENT_SLO_EXCLUDE_CANCELLED and "cancelled" in terminal:
        terminal.remove("cancelled")
    q = (
        db.query(
            AgentRun.organization_id.label("org_id"),
            AgentRun.status.label("status"),
            func.count().label("count"),
        )
        .filter(
            AgentRun.created_at >= start,
            AgentRun.created_at < end,
            AgentRun.status.in_(terminal),
        )
        .group_by(AgentRun.organization_id, AgentRun.status)
        .all()
    )
    return [
        {
            "metric_name": METRIC_AGENT_RUNS,
            "org_id": row.org_id,
            "labels": {"status": row.status or "unknown"},
            "count": row.count,
            "numerator": row.count if row.status == "completed" else 0,
            "denominator": row.count,
        }
        for row in q
    ]


def _rows_notifications(db: Session, start: datetime, end: datetime) -> list[dict]:
    q = (
        db.query(
            LegalNotificationEvent.organization_id.label("org_id"),
            LegalNotificationEvent.channel.label("channel"),
            LegalNotificationEvent.event_type.label("event_type"),
            LegalNotificationEvent.status.label("status"),
            func.count().label("count"),
        )
        .filter(
            LegalNotificationEvent.created_at >= start,
            LegalNotificationEvent.created_at < end,
            LegalNotificationEvent.status.in_(_NOTIFICATION_DENOMINATOR),
        )
        .group_by(
            LegalNotificationEvent.organization_id,
            LegalNotificationEvent.channel,
            LegalNotificationEvent.event_type,
            LegalNotificationEvent.status,
        )
        .all()
    )
    return [
        {
            "metric_name": METRIC_NOTIFICATION_DELIVERIES,
            "org_id": row.org_id,
            "labels": {"channel": row.channel or "unknown", "event_type": row.event_type or "unknown",
                       "status": row.status or "unknown"},
            "count": row.count,
            "numerator": row.count if row.status in _NOTIFICATION_NUMERATOR else 0,
            "denominator": row.count,
        }
        for row in q
    ]


def _rows_task_outcomes(db: Session, start: datetime, end: datetime) -> list[dict]:
    q = (
        db.query(
            TaskRun.tenant_id.label("org_id"),
            TaskRun.queue.label("queue"),
            TaskRun.scope.label("scope"),
            TaskRun.status.label("status"),
            func.count().label("count"),
        )
        .filter(
            TaskRun.created_at >= start,
            TaskRun.created_at < end,
            TaskRun.status.in_(_TASK_TERMINAL),
        )
        .group_by(TaskRun.tenant_id, TaskRun.queue, TaskRun.scope, TaskRun.status)
        .all()
    )
    return [
        {
            "metric_name": METRIC_TASK_OUTCOMES,
            "org_id": row.org_id,
            "labels": {"queue": row.queue or "default", "scope": row.scope or "task",
                       "status": row.status or "unknown"},
            "count": row.count,
            "numerator": row.count if row.status == "succeeded" else 0,
            "denominator": row.count,
        }
        for row in q
    ]


def _rows_model_cost(db: Session, start: datetime, end: datetime) -> list[dict]:
    q = (
        db.query(
            CostLedgerEntry.tenant_id.label("org_id"),
            TokenUsage.model.label("model"),
            CostLedgerEntry.currency.label("currency"),
            func.count().label("count"),
            func.sum(CostLedgerEntry.amount).label("cost_value"),
        )
        .join(TokenUsage, TokenUsage.id == CostLedgerEntry.source_id)
        .filter(
            CostLedgerEntry.entry_type == "llm_call",
            CostLedgerEntry.created_at >= start,
            CostLedgerEntry.created_at < end,
        )
        .group_by(CostLedgerEntry.tenant_id, TokenUsage.model, CostLedgerEntry.currency)
        .all()
    )
    return [
        {
            "metric_name": METRIC_MODEL_COST,
            "org_id": row.org_id,
            "labels": {"model": row.model or "unknown", "currency": row.currency or "CNY"},
            "count": row.count,
            "cost_value": row.cost_value,
        }
        for row in q
    ]


# ── 快照源聚合（ops_metric_snapshots → 小时/天级）───────────────────────────

def _rows_from_snapshots(db: Session, metric_name: str, start: datetime, end: datetime) -> list[dict]:
    rows = (
        db.query(
            OpsMetricSnapshot.org_id.label("org_id"),
            OpsMetricSnapshot.labels_json.label("labels_json"),
            func.sum(OpsMetricSnapshot.count).label("count"),
            func.sum(OpsMetricSnapshot.sum_value).label("sum_value"),
            func.max(OpsMetricSnapshot.p95_value).label("p95_value"),
            func.max(OpsMetricSnapshot.count).label("max_value"),
        )
        .filter(
            OpsMetricSnapshot.metric_name == metric_name,
            OpsMetricSnapshot.bucket_start >= start,
            OpsMetricSnapshot.bucket_start < end,
        )
        .group_by(OpsMetricSnapshot.org_id, OpsMetricSnapshot.labels_json)
        .all()
    )
    items: list[dict] = []
    for row in rows:
        try:
            labels = json.loads(row.labels_json) if row.labels_json else {}
        except (TypeError, ValueError):
            labels = {}
        if not isinstance(labels, dict):
            labels = {}
        if metric_name == METRIC_API_REQUEST_DURATION:
            items.append({
                "metric_name": metric_name,
                "org_id": row.org_id,
                "labels": labels,
                "count": row.count,
                "sum_value": row.sum_value,
                "p95_value": row.p95_value,
            })
        else:  # gauge 类（积压量）：窗口内最大值
            items.append({
                "metric_name": metric_name,
                "org_id": row.org_id,
                "labels": labels,
                "count": row.count,
                "max_value": row.max_value,
            })
    return items


def _compute_rows(db: Session, metric_name: str, start: datetime, end: datetime) -> list[dict]:
    if metric_name in _SNAPSHOT_SOURCE_METRICS:
        return _rows_from_snapshots(db, metric_name, start, end)
    source = {
        METRIC_LLM_CALLS: _rows_llm_calls,
        METRIC_DOC_PARSE: _rows_doc_parse,
        METRIC_AGENT_RUNS: _rows_agent_runs,
        METRIC_NOTIFICATION_DELIVERIES: _rows_notifications,
        METRIC_TASK_OUTCOMES: _rows_task_outcomes,
        METRIC_MODEL_COST: _rows_model_cost,
    }.get(metric_name)
    if source is None:
        return []
    return source(db, start, end)


# ── 幂等落桶 ─────────────────────────────────────────────────────────────────

def _model_for(granularity: str):
    return OpsMetricHourly if granularity == GRANULARITY_HOUR else OpsMetricDaily


def _upsert_bucket(db: Session, granularity: str, metric_name: str, bucket: datetime, rows: Iterable[dict]) -> int:
    """先删后插（事务内幂等）：重复执行/重算结果一致，不重复累加。"""
    model = _model_for(granularity)
    db.query(model).filter(
        model.bucket_start == bucket,
        model.metric_name == metric_name,
    ).delete(synchronize_session=False)
    inserted = 0
    for row in rows:
        db.add(model(
            bucket_start=bucket,
            metric_name=row["metric_name"],
            org_id=row.get("org_id"),
            labels_json=_labels_json(row.get("labels") or {}),
            count=row.get("count") or 0,
            sum_value=_to_decimal(row.get("sum_value")),
            max_value=_to_decimal(row.get("max_value")),
            p95_value=_to_decimal(row.get("p95_value")),
            numerator=_to_decimal(row.get("numerator")),
            denominator=_to_decimal(row.get("denominator")),
            cost_value=_to_decimal(row.get("cost_value")),
            source_watermark=row.get("source_watermark"),
            schema_version=1,
        ))
        inserted += 1
    db.commit()
    return inserted


def _advance_watermark(db: Session, granularity: str, metric_name: str, bucket: datetime) -> None:
    row = (
        db.query(OpsMetricWatermark)
        .filter(OpsMetricWatermark.granularity == granularity,
                OpsMetricWatermark.metric_name == metric_name)
        .first()
    )
    if row is None:
        db.add(OpsMetricWatermark(granularity=granularity, metric_name=metric_name, last_bucket=bucket))
    elif bucket > row.last_bucket:
        row.last_bucket = bucket
    db.commit()


def _watermark_or_backfill_start(db: Session, granularity: str, metric_name: str, now: datetime) -> datetime:
    row = (
        db.query(OpsMetricWatermark)
        .filter(OpsMetricWatermark.granularity == granularity,
                OpsMetricWatermark.metric_name == metric_name)
        .first()
    )
    if row is not None and row.last_bucket is not None:
        return next_bucket(row.last_bucket, granularity)
    settings = get_settings()
    if granularity == GRANULARITY_HOUR:
        days = settings.OBS_AGGREGATION_HOURLY_RETENTION_DAYS
    else:
        days = settings.OBS_AGGREGATION_DAILY_RETENTION_DAYS
    return floor_bucket(now - timedelta(days=days), granularity)


def aggregate_metric(db: Session, granularity: str, metric_name: str, *, now: datetime | None = None) -> dict:
    """从水位线推进到最近完整桶（含），逐桶幂等聚合；失败只影响单指标，可断点恢复。"""
    now = now or _utcnow()
    cursor = _watermark_or_backfill_start(db, granularity, metric_name, now)
    latest_complete = floor_bucket(now, granularity)
    buckets = 0
    while cursor < latest_complete:
        end = bucket_end(cursor, granularity)
        rows = _compute_rows(db, metric_name, cursor, end)
        _upsert_bucket(db, granularity, metric_name, cursor, rows)
        _advance_watermark(db, granularity, metric_name, cursor)
        buckets += 1
        cursor = end
    return {"granularity": granularity, "metric_name": metric_name, "buckets": buckets}


def aggregate_all(db: Session, granularity: str, *, now: datetime | None = None) -> dict:
    """聚合全部 SLO 指标（单指标异常不影响其他指标）。"""
    now = now or _utcnow()
    results: dict[str, dict] = {}
    metrics = sorted(_SNAPSHOT_SOURCE_METRICS | {
        METRIC_LLM_CALLS, METRIC_DOC_PARSE, METRIC_AGENT_RUNS,
        METRIC_NOTIFICATION_DELIVERIES, METRIC_TASK_OUTCOMES, METRIC_MODEL_COST,
    })
    for metric_name in metrics:
        try:
            results[metric_name] = aggregate_metric(db, granularity, metric_name, now=now)
        except Exception as exc:  # noqa: BLE001 - 单指标失败不阻断其余指标
            logger.warning("ops aggregation failed granularity=%s metric=%s: %s",
                           granularity, metric_name, type(exc).__name__)
            results[metric_name] = {"granularity": granularity, "metric_name": metric_name, "buckets": 0, "error": True}
    return results


# ── 查询层（运营统计/SLO 报表数据源，读聚合表不扫明细）──────────────────────

def slo_series(db: Session, *, metric_name: str, days: int = 30, org_id: int | None = None) -> list[dict]:
    """读天级聚合返回每日系列；org_id 过滤支持租户隔离。"""
    since = floor_bucket(_utcnow() - timedelta(days=days), GRANULARITY_DAY)
    q = db.query(OpsMetricDaily).filter(
        OpsMetricDaily.metric_name == metric_name,
        OpsMetricDaily.bucket_start >= since,
    )
    if org_id is not None:
        q = q.filter(OpsMetricDaily.org_id == org_id)
    rows = q.order_by(OpsMetricDaily.bucket_start.asc()).all()
    return [
        {
            "bucket": row.bucket_start.isoformat(),
            "org_id": row.org_id,
            "labels": json.loads(row.labels_json) if row.labels_json else {},
            "count": float(row.count or 0),
            "sum_value": float(row.sum_value) if row.sum_value is not None else None,
            "max_value": float(row.max_value) if row.max_value is not None else None,
            "p95_value": float(row.p95_value) if row.p95_value is not None else None,
            "numerator": float(row.numerator) if row.numerator is not None else None,
            "denominator": float(row.denominator) if row.denominator is not None else None,
            "cost_value": float(row.cost_value) if row.cost_value is not None else None,
        }
        for row in rows
    ]


def slo_rates(db: Session, *, metric_name: str, days: int = 30, org_id: int | None = None) -> dict:
    """按 (org, labels) 汇总窗口内 numerator/denominator，返回成功率/完成率口径。"""
    series = slo_series(db, metric_name=metric_name, days=days, org_id=org_id)
    by_key: dict[tuple, dict] = {}
    for item in series:
        key = (item["org_id"], json.dumps(item["labels"], sort_keys=True, ensure_ascii=False))
        entry = by_key.setdefault(key, {"org_id": item["org_id"], "labels": item["labels"],
                                        "numerator": 0.0, "denominator": 0.0, "count": 0.0})
        entry["numerator"] += item["numerator"] or 0.0
        entry["denominator"] += item["denominator"] or 0.0
        entry["count"] += item["count"] or 0.0
    result = []
    for entry in by_key.values():
        denom = entry["denominator"]
        result.append({
            "org_id": entry["org_id"],
            "labels": entry["labels"],
            "numerator": entry["numerator"],
            "denominator": denom,
            "rate": round(entry["numerator"] / denom, 6) if denom else None,
            "count": entry["count"],
        })
    return {"metric": metric_name, "days": days, "items": result}


ops_aggregation_service = type("_Svc", (), {
    "aggregate_metric": staticmethod(aggregate_metric),
    "aggregate_all": staticmethod(aggregate_all),
    "slo_series": staticmethod(slo_series),
    "slo_rates": staticmethod(slo_rates),
    "floor_bucket": staticmethod(floor_bucket),
    "next_bucket": staticmethod(next_bucket),
})()
