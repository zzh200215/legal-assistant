"""连接器同步运行框架：分页拉取 → 增量去重 → sink 落地 → 整批成功后推进 cursor。

可靠性与幂等关键点：
- **cursor 唯一推进点**：本批所有 item 幂等登记 + sink 落地 + ``connector_sync_items``
  upsert 全部成功、整批提交后，才同事务写 ``run.cursor_json/checkpoint_json`` +
  ``connector.sync_cursor_json``。任一 item 失败 → 不推进 cursor，run 置 failed
  + attempt+1 + next_retry_at，本批幂等键标记 failed（否则重试会撞 in_progress 冲突）。
- **断点恢复**：已成功批次已持久化；重跑从 ``connector.sync_cursor_json`` 续，
  已提交对象按 ``version_hash`` 跳过，绝不重复写 sink。
- 并发互斥由 ``connector_sync_task`` 的分布式锁 + ``lease_owner/lease_expires_at`` 承担。

仅演示/测试使用（mock 连接器），不复活已删除的入站连接器。
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Callable

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.observability_sanitizer import truncate_text
from app.core.time import utc_now
from app.models.connector import ExternalConnector
from app.models.connector_sync_item import ConnectorSyncItem
from app.models.sync_run import SyncRun
from app.services.jobs.idempotency_service import IdempotencyService, idempotency_service
from app.services.integration.mock_connector_client import build_mock_client, build_mock_sink

IDEM_ITEM_SCOPE = "connector_sync_item"
IDEM_RUN_SCOPE = "connector_sync_run"


def _version_hash(external_id: str, version: str) -> str:
    return hashlib.sha256(f"{external_id}:{version}".encode()).hexdigest()


def _error_code(exc: Exception) -> str:
    return type(exc).__name__


def _retry_delay_seconds(attempt: int) -> int:
    settings = get_settings()
    base = int(settings.SYNC_BACKOFF_BASE_SECONDS)
    return min(base * (2 ** max(0, attempt - 1)), 3600)


def _parse_cursor(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def get_or_create_run(
    db: Any,
    *,
    connector: ExternalConnector,
    owner: str,
    sync_mode: str,
    ttl_seconds: int,
) -> SyncRun | None:
    """取最近一次未成功的 run 复用；最近已成功则返回 None（幂等跳过）。"""
    latest = (
        db.query(SyncRun)
        .filter(SyncRun.connector_id == connector.id)
        .order_by(SyncRun.id.desc())
        .first()
    )
    if latest is not None and latest.status == "succeeded":
        return None
    run = latest
    if run is None:
        run = SyncRun(
            connector_id=connector.id,
            user_id=connector.user_id,
            sync_mode=sync_mode,
            status="running",
            idempotency_key=f"sync:{connector.id}:{utc_now().strftime('%Y%m%dT%H%M%S')}",
        )
        db.add(run)
    run.status = "running"
    run.lease_owner = owner
    run.started_at = run.started_at or utc_now()
    run.lease_expires_at = utc_now() + timedelta(seconds=ttl_seconds)
    # P1 链路关联：统一上下文 trace_id（缺失不伪造）。
    if run.trace_id is None:
        try:
            from app.core.obs_context import get_context

            ctx = get_context()
            run.trace_id = ctx.trace_id
        except Exception:  # noqa: BLE001
            pass
    db.commit()
    db.refresh(run)
    return run


def _process_batch(db: Any, *, run: SyncRun, connector_id: int, page: Any,
                   sink: Any, idem: IdempotencyService) -> dict:
    """处理一批：不变跳过 / 变化 upsert。整批任一项失败则本批回滚并 re-raise。"""
    processed = 0
    succeeded = 0
    for item in page.items:
        external_id = str(item["external_id"])
        version = str(item.get("version") or "")
        version_hash = _version_hash(external_id, version)
        row = (
            db.query(ConnectorSyncItem)
            .filter(
                ConnectorSyncItem.connector_id == connector_id,
                ConnectorSyncItem.external_id == external_id,
            )
            .first()
        )
        if row is not None and row.version_hash == version_hash:
            processed += 1  # 未变化：跳过，不写 sink
            continue
        key = f"{connector_id}:{external_id}:{version_hash[:16]}"
        try:
            idem.begin(db, scope=IDEM_ITEM_SCOPE, key=key, request_hash=version_hash)
            sink.write(db, connector_id, item)
            if row is None:
                db.add(ConnectorSyncItem(
                    connector_id=connector_id,
                    external_id=external_id,
                    version_hash=version_hash,
                    sync_run_id=run.id,
                    last_synced_at=utc_now(),
                ))
            else:
                row.version_hash = version_hash
                row.sync_run_id = run.id
                row.last_synced_at = utc_now()
            db.flush()
            idem.complete(db, scope=IDEM_ITEM_SCOPE, key=key, response_snapshot=external_id)
            processed += 1
            succeeded += 1
        except Exception:
            db.rollback()
            try:
                idem.fail(db, scope=IDEM_ITEM_SCOPE, key=key)
            except Exception:  # noqa: BLE001 - 幂等键清理失败不掩盖原始错误
                db.rollback()
            raise
    return {"processed": processed, "succeeded": succeeded}


def _fail_run(db: Any, run: SyncRun, exc: Exception, *, idem: IdempotencyService) -> None:
    run.status = "failed"
    run.error_code = _error_code(exc)
    run.error_message = truncate_text(f"{type(exc).__name__}: {str(exc)}", 500)
    run.attempt = (run.attempt or 0) + 1
    run.next_retry_at = utc_now() + timedelta(seconds=_retry_delay_seconds(run.attempt))
    run.completed_at = utc_now()
    db.commit()
    idem.fail(db, scope=IDEM_RUN_SCOPE, key=run.idempotency_key)
    _record_sync_metric("failed")


def _record_sync_metric(status: str) -> None:
    """连接器同步终态指标（P1，非阻塞）。"""
    try:
        from app.core.metrics import metrics

        metrics.increment("connector_syncs", labels={"status": status})
    except Exception:  # noqa: BLE001 - 指标失败不影响业务
        pass


def run_sync_run(
    *,
    db: Any,
    run: SyncRun,
    connector: ExternalConnector,
    client: Any,
    sink: Any,
    batch_size: int = 100,
    lease_refresh: Callable[[], None] | None = None,
    idem: IdempotencyService | None = None,
) -> dict:
    """执行一轮同步：分页 → 批量去重落地 → 推进 cursor，直至 has_more=False。"""
    idem = idem or idempotency_service
    connector_id = connector.id
    idem.begin(db, scope=IDEM_RUN_SCOPE, key=run.idempotency_key, request_hash=f"connector:{connector_id}")
    cursor = _parse_cursor(connector.sync_cursor_json)
    page_no = 0
    # P1：连接器同步子 span（仅 connector_type/sync_mode 元数据，不含凭证/正文）。
    from app.core.telemetry import observe_span

    with observe_span("connector.sync", attributes={
        "connector_type": connector.connector_type,
        "sync_mode": run.sync_mode,
    }):
        try:
            while True:
                page_no += 1
                page = client.page(connector_id, cursor, batch_size)
                if lease_refresh:
                    lease_refresh()
                if not page.items:
                    break
                batch = _process_batch(db, run=run, connector_id=connector_id, page=page,
                                       sink=sink, idem=idem)
                # 整批成功 → 唯一 cursor 推进点（与计数同事务提交）。
                # 末批 next_cursor=None 时保留上一真实游标，不写入 "null"。
                run.processed = (run.processed or 0) + batch["processed"]
                run.succeeded = (run.succeeded or 0) + batch["succeeded"]
                cursor = page.next_cursor
                if cursor is not None:
                    run.cursor_json = json.dumps(cursor)
                    run.checkpoint_json = json.dumps({"page": page_no, "cursor": cursor})
                    run.source_version = page.source_version
                    connector.sync_cursor_json = json.dumps(cursor)
                db.commit()
                if lease_refresh:
                    lease_refresh()
                if not page.has_more or page.next_cursor is None:
                    break
            run.status = "succeeded"
            run.completed_at = utc_now()
            run.result_summary = f"同步完成：处理 {run.processed} 项，新增/更新 {run.succeeded} 项"
            db.commit()
            idem.complete(db, scope=IDEM_RUN_SCOPE, key=run.idempotency_key,
                          response_snapshot=run.result_summary)
            _record_sync_metric("succeeded")
            return {
                "status": "succeeded",
                "processed": run.processed,
                "succeeded": run.succeeded,
                "failed": run.failed,
            }
        except Exception as exc:  # noqa: BLE001 - 中断/故障统一记账后 re-raise 交给 Celery 重试
            _fail_run(db, run, exc, idem=idem)
            raise


def _run_connector_sync(
    connector_id: int,
    sync_mode: str,
    trigger_id: int | None,
    *,
    token: str | None,
) -> dict:
    """任务实体：取 run → 构建 client/sink → run_sync_run。"""
    settings = get_settings()
    ttl = int(settings.SYNC_RUN_LEASE_TTL_SECONDS)
    db = SessionLocal()
    try:
        connector = (
            db.query(ExternalConnector)
            .filter(ExternalConnector.id == connector_id)
            .first()
        )
        if connector is None:
            return {"status": "error", "reason": "connector_not_found"}
        run = get_or_create_run(
            db, connector=connector, owner=token or "manual",
            sync_mode=sync_mode, ttl_seconds=ttl,
        )
        if run is None:
            return {"status": "skipped", "reason": "already_succeeded"}

        def refresh() -> None:
            if token:
                from app.tasks.runtime import renew_task_lock
                renew_task_lock("connector_sync", scope=f"conn:{connector_id}",
                                token=token, ttl_seconds=ttl)
            run.lease_expires_at = utc_now() + timedelta(seconds=ttl)
            db.commit()

        client = build_mock_client(connector)
        sink = build_mock_sink(connector)
        return run_sync_run(
            db=db, run=run, connector=connector, client=client, sink=sink,
            batch_size=int(settings.SYNC_DEFAULT_BATCH_SIZE),
            lease_refresh=refresh,
        )
    finally:
        db.close()
