"""P1 可观测性任务：指标快照 / 预聚合 / 审计导出消费。

- snapshot_ops_metrics：进程内 metrics facade 窗口快照落库（ops_metric_snapshots），
  并采集任务积压（DB 口径 + Redis Broker LLEN 口径，两者分开记录）。
- aggregate_ops_metrics：小时/天级幂等预聚合（水位线断点恢复）。
- run_audit_export：LegalAsyncJob(audit_export) 消费端（分页流式导出）。
全部任务非阻塞：任何异常只记日志，不影响其他任务/业务。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import redis

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.metrics import metrics
from app.core.time import utc_now
from app.models.ops_metric import OpsMetricSnapshot
from app.models.task_run import TaskRun
from app.services.observability.audit_export_service import audit_export_service
from app.services.observability.ops_aggregation_service import ops_aggregation_service
from app.tasks.runtime import beat_lock, record_beat_heartbeat

logger = logging.getLogger(__name__)


@celery_app.task(name="snapshot_ops_metrics")
def snapshot_ops_metrics_task() -> dict:
    return snapshot_ops_metrics()


@celery_app.task(name="aggregate_ops_metrics")
def aggregate_ops_metrics_task() -> dict:
    return aggregate_ops_metrics()


@celery_app.task(name="run_audit_export")
def run_audit_export_task(job_id: int) -> dict:
    return run_audit_export(job_id)

# Broker 队列清单（与 celery_app._QUEUE_LIMITS 对齐，用于 LLEN 积压 gauge）。
_BROKER_QUEUES = ("document", "llm", "connector", "notification", "billing")


@beat_lock(task_name="snapshot_ops_metrics", ttl_seconds=900)
def snapshot_ops_metrics() -> dict:
    """窗口快照：metrics facade 增量落库 + 任务积压 gauge（DB/Broker 分列）。"""
    settings = get_settings()
    if not settings.OBS_METRICS_SNAPSHOT_ENABLED:
        return {"enabled": False}

    bucket_start = _snapshot_bucket_start(settings.OBS_METRICS_SNAPSHOT_WINDOW_SECONDS)
    items = metrics.snapshot_and_reset()

    # 任务积压（DB 口径）：running/retrying 且超过陈旧阈值的视为 claimed-but-expired。
    backlog_rows = _db_backlog_rows()
    broker_rows = _broker_backlog_rows()
    if not items and not backlog_rows and not broker_rows:
        return {"bucket": bucket_start.isoformat(), "items": 0}

    db = SessionLocal()
    try:
        written = 0
        for item in items:
            db.add(_snapshot_row(bucket_start, item))
            written += 1
        for row in backlog_rows:
            db.add(_snapshot_row(bucket_start, row))
            written += 1
        for row in broker_rows:
            db.add(_snapshot_row(bucket_start, row))
            written += 1
        db.commit()
        return {"bucket": bucket_start.isoformat(), "items": written}
    except Exception:  # noqa: BLE001 - 快照失败不影响业务
        logger.warning("ops metric snapshot persist failed", exc_info=True)
        db.rollback()
        return {"bucket": bucket_start.isoformat(), "items": 0, "error": True}
    finally:
        db.close()


def _snapshot_bucket_start(window_seconds: int) -> datetime:
    """按窗口对齐桶起点（naive UTC）。"""
    now = utc_now()
    bucket_ms = int(window_seconds) * 1000
    aligned = int(now.timestamp() * 1000) // bucket_ms * bucket_ms
    return datetime.fromtimestamp(aligned / 1000, tz=timezone.utc).replace(tzinfo=None)


def _snapshot_row(bucket_start, item: dict) -> OpsMetricSnapshot:
    labels = item.get("labels") or {}
    return OpsMetricSnapshot(
        bucket_start=bucket_start,
        metric_name=item["metric_name"],
        org_id=labels.pop("org", None) if isinstance(labels, dict) else None,
        kind=item.get("kind", "counter"),
        labels_json=json.dumps(labels, ensure_ascii=False, sort_keys=True) if labels else None,
        count=item.get("count") or 0,
        sum_value=item.get("sum_value"),
        p95_value=item.get("p95_value"),
        numerator=item.get("numerator"),
        denominator=item.get("denominator"),
    )


def _db_backlog_rows() -> list[dict]:
    """DB 口径积压：task_runs 中 running/retrying 按 queue 分组计数。

    state=claimed_but_expired：updated_at 早于陈旧阈值（worker 崩溃未回收）。
    """
    settings = get_settings()
    stale_before = utc_now() - timedelta(minutes=settings.OBS_BACKLOG_STALE_MINUTES)
    db = SessionLocal()
    try:
        rows = (
            db.query(
                TaskRun.queue.label("queue"),
                TaskRun.status.label("status"),
                (TaskRun.updated_at < stale_before).label("is_stale"),
            )
            .filter(TaskRun.status.in_(("running", "retrying")))
            .all()
        )
    finally:
        db.close()
    counts: dict[tuple, list[int]] = {}
    for row in rows:
        key = (row.queue or "default", bool(row.is_stale))
        bucket = counts.setdefault(key, [0, 0])
        bucket[0] += 1
        if row.is_stale:
            bucket[1] += 1
    items: list[dict] = []
    for (queue, stale), (total, expired) in counts.items():
        items.append({
            "metric_name": "task_backlog",
            "labels": {"queue": queue, "state": "claimed_but_expired" if stale else "in_flight"},
            "kind": "gauge",
            "count": expired if stale else total,
        })
    return items


def _broker_backlog_rows() -> list[dict]:
    """Broker 口径积压：Redis LLEN 按队列（与 DB 口径分开，不混算）。"""
    try:
        client = redis.from_url(get_settings().REDIS_URL, decode_responses=True)
        items = []
        for queue in _BROKER_QUEUES:
            try:
                length = int(client.llen(queue) or 0)
            except Exception:  # noqa: BLE001 - 单队列失败跳过
                length = 0
            items.append({
                "metric_name": "broker_backlog",
                "labels": {"queue": queue},
                "kind": "gauge",
                "count": length,
            })
        return items
    except Exception:  # noqa: BLE001 - Redis 不可用按 0 处理（与既有锁降级语义一致）
        return []


@beat_lock(task_name="aggregate_ops_metrics", ttl_seconds=3300)
def aggregate_ops_metrics() -> dict:
    """小时 + 天级幂等预聚合（逐桶先删后插，水位线断点恢复）。"""
    db = SessionLocal()
    try:
        hourly = ops_aggregation_service.aggregate_all(db, "hour")
        daily = ops_aggregation_service.aggregate_all(db, "day")
        return {"hourly": hourly, "daily": daily}
    except Exception:  # noqa: BLE001 - 聚合失败不阻断其他任务
        logger.warning("ops aggregation task failed", exc_info=True)
        return {"error": True}
    finally:
        db.close()


def run_audit_export(job_id: int) -> dict:
    """审计导出任务消费端（LegalAsyncJob audit_export）。"""
    db = SessionLocal()
    try:
        return audit_export_service.run_export_job(db, job_id)
    except Exception as exc:  # noqa: BLE001 - 任务失败已记账，不抛给 Celery 重试风暴
        logger.warning("audit export job %s failed: %s", job_id, type(exc).__name__)
        return {"status": "failed", "job_id": job_id, "error": type(exc).__name__}
    finally:
        db.close()



@celery_app.task(name="dispatch_operational_alerts")
@beat_lock(task_name="dispatch_operational_alerts", ttl_seconds=600)
def dispatch_operational_alerts_task():
    record_beat_heartbeat()
    db = SessionLocal()
    try:
        from app.services.notification.operational_alert_service import operational_alert_service

        return operational_alert_service.dispatch(db=db)
    finally:
        db.close()


@celery_app.task(name="run_database_archive")
@beat_lock(task_name="run_database_archive", ttl_seconds=86400)
def run_database_archive_task():
    """按表保留策略批量清理过期日志/用量记录。

    默认关闭且 dry-run；DATABASE_ARCHIVE_ENABLED=true 且 DRY_RUN=false 才真实删除。
    使用统一事务上下文 session_scope；慢 SQL 日志通过 correlation id 关联到本任务。
    """
    record_beat_heartbeat()
    from app.core.database import session_scope
    from app.core.db_monitor import set_db_correlation_id

    run_key = f"archive-{utc_now().strftime('%Y%m%dT%H%M%S')}"
    set_db_correlation_id(run_key)
    try:
        with session_scope() as db:
            from app.services.documents.archive_service import archive_service
            from app.services.jobs.idempotency_service import idempotency_service

            result = archive_service.run(db=db)
            # 幂等键 TTL 清理（分批、幂等，可随归档任务安全执行）
            result["expired_idempotency_keys_deleted"] = idempotency_service.cleanup_expired(db)
            return result
    finally:
        set_db_correlation_id(None)
