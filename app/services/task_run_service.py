"""任务运行台账服务：task_runs 的创建 / 状态推进 / 查询。

task_runs 是**多 run 状态台账**（不做唯一约束）：同一逻辑任务多次执行各记一行，
幂等由 ``idempotency_keys`` 保证。Celery 重试复用同一 task_id，故 ``start`` 按
task_id upsert：重试时更新回 running 并递增 attempt，不重复建行。
错误信息统一截断脱敏（不落 token/密钥/完整正文）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.observability_sanitizer import truncate_text
from app.models.task_run import TaskRun


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sanitized(message: str | None) -> str | None:
    return truncate_text(message, 2000) if message else None


class TaskRunService:
    def start(
        self,
        db: Session,
        *,
        task_id: str,
        task_name: str,
        scope: str = "task",
        queue: str | None = None,
        business_key: str | None = None,
        tenant_id: int | None = None,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
        attempt: int = 1,
        trace_id: str | None = None,
    ) -> TaskRun:
        """登记一次运行。同 task_id 已存在（重试/重投）时更新回 running 并递增 attempt。"""
        now = _utcnow()
        row = db.query(TaskRun).filter(TaskRun.task_id == task_id).first()
        if row is None:
            row = TaskRun(
                task_id=task_id,
                task_name=task_name,
                scope=scope,
                queue=queue,
                business_key=business_key,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                max_attempts=max_attempts,
                status="running",
                attempt=attempt,
                started_at=now,
                trace_id=trace_id or task_id,
            )
            db.add(row)
        else:
            row.task_name = task_name
            row.scope = scope or row.scope
            row.queue = queue or row.queue
            row.business_key = business_key or row.business_key
            if tenant_id is not None:
                row.tenant_id = tenant_id
            row.idempotency_key = idempotency_key or row.idempotency_key
            if row.max_attempts is None and max_attempts:
                row.max_attempts = max_attempts
            row.status = "running"
            row.attempt = attempt if attempt > 1 else (row.attempt or 0) + 1
            row.error_code = None
            row.error_message = None
            row.next_retry_at = None
            row.finished_at = None
            row.duration_ms = None
        db.commit()
        db.refresh(row)
        return row

    def update_checkpoint(self, db: Session, *, task_id: str, checkpoint_json: str | None) -> None:
        row = self._by_task_id(db, task_id)
        if row is None:
            return
        row.checkpoint_json = checkpoint_json
        db.commit()

    def mark_succeeded(self, db: Session, *, task_id: str) -> TaskRun | None:
        row = self._by_task_id(db, task_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "succeeded"
        row.finished_at = now
        if row.started_at:
            row.duration_ms = int((now - row.started_at).total_seconds() * 1000)
        db.commit()
        return row

    def mark_failed(
        self,
        db: Session,
        *,
        task_id: str,
        error_code: str | None = None,
        error_message: str | None = None,
        attempt: int | None = None,
        next_retry_at: datetime | None = None,
    ) -> TaskRun | None:
        row = self._by_task_id(db, task_id)
        if row is None:
            return None
        now = _utcnow()
        row.status = "failed"
        row.error_code = error_code or row.error_code
        if error_message is not None:
            row.error_message = _sanitized(error_message)
        if attempt:
            row.attempt = attempt
        row.next_retry_at = next_retry_at
        row.finished_at = now
        if row.started_at:
            row.duration_ms = int((now - row.started_at).total_seconds() * 1000)
        db.commit()
        return row

    def mark_retrying(
        self,
        db: Session,
        *,
        task_id: str,
        error_code: str | None = None,
        error_message: str | None = None,
        attempt: int | None = None,
        next_retry_at: datetime | None = None,
    ) -> TaskRun | None:
        row = self._by_task_id(db, task_id)
        if row is None:
            return None
        row.status = "retrying"
        if error_code:
            row.error_code = error_code
        if error_message is not None:
            row.error_message = _sanitized(error_message)
        if attempt:
            row.attempt = attempt
        row.next_retry_at = next_retry_at
        db.commit()
        return row

    def get_latest_by_business_key(self, db: Session, *, task_name: str, business_key: str) -> TaskRun | None:
        return (
            db.query(TaskRun)
            .filter(TaskRun.task_name == task_name, TaskRun.business_key == business_key)
            .order_by(TaskRun.started_at.desc(), TaskRun.id.desc())
            .first()
        )

    def cancelled_or_superseded(self, db: Session, *, task_name: str, business_key: str) -> bool:
        """重试前检查：同业务键的最新一次运行已成功 → 旧重试应跳过（避免重复副作用）。"""
        latest = self.get_latest_by_business_key(db, task_name=task_name, business_key=business_key)
        return latest is not None and latest.status == "succeeded"

    def list_for_tenant(
        self, db: Session, *, tenant_id: int, status: str | None = None, limit: int = 50
    ) -> list[TaskRun]:
        """租户范围查询（满足权限约束）；status 可选过滤。"""
        query = db.query(TaskRun).filter(TaskRun.tenant_id == tenant_id)
        if status:
            query = query.filter(TaskRun.status == status)
        return query.order_by(TaskRun.started_at.desc(), TaskRun.id.desc()).limit(limit).all()

    def cleanup_expired(self, db: Session, *, batch_size: int | None = None) -> int:
        """按 TASK_RUNS_RETENTION_DAYS 清理过期运行记录，可安全重复调用。"""
        days = int(get_settings().TASK_RUNS_RETENTION_DAYS)
        cutoff = _utcnow() - timedelta(days=days)
        count = db.query(TaskRun).filter(TaskRun.created_at < cutoff).delete(synchronize_session=False)
        db.commit()
        return count

    @staticmethod
    def _by_task_id(db: Session, task_id: str) -> TaskRun | None:
        return db.query(TaskRun).filter(TaskRun.task_id == task_id).first()


task_run_service = TaskRunService()
