from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.email import EmailDraft
from app.models.connector import MailboxMessage
from app.models.schedule import ScheduledWorkflow, WorkflowExecution
from app.models.task import Task
from app.models.user import User
from app.services.oplog_service import oplog_service
from app.services.workflow_service import workflow_service


class SchedulerService:
    WORKFLOW_TYPES = {"daily_mail_digest", "weekly_report", "meeting_followup"}
    FREQUENCIES = {"daily", "weekly"}

    @staticmethod
    def _json(value: str | None) -> dict:
        try:
            payload = json.loads(value or "{}")
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _validate_time(value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("run_time 必须为 HH:MM") from exc
        return parsed.strftime("%H:%M")

    @staticmethod
    def _zone(value: str) -> ZoneInfo:
        try:
            return ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("不支持的时区") from exc

    def _next_run(self, schedule: ScheduledWorkflow, now: datetime | None = None) -> datetime:
        current = (now or datetime.now(timezone.utc)).astimezone(self._zone(schedule.timezone))
        hour, minute = [int(part) for part in schedule.run_time.split(":")]
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if schedule.frequency == "weekly":
            weekday = schedule.weekday if schedule.weekday is not None else 0
            candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
        if candidate <= current:
            candidate += timedelta(days=7 if schedule.frequency == "weekly" else 1)
        return candidate.astimezone(timezone.utc)

    def _can_access(self, schedule: ScheduledWorkflow, user: User) -> bool:
        return user.role == "admin" or schedule.user_id == user.id

    def serialize_schedule(self, schedule: ScheduledWorkflow) -> dict:
        return {
            "id": schedule.id,
            "user_id": schedule.user_id,
            "organization_id": schedule.organization_id,
            "department_id": schedule.department_id,
            "name": schedule.name,
            "workflow_type": schedule.workflow_type,
            "frequency": schedule.frequency,
            "run_time": schedule.run_time,
            "weekday": schedule.weekday,
            "timezone": schedule.timezone,
            "config": self._json(schedule.config_json),
            "enabled": schedule.enabled,
            "next_run_at": schedule.next_run_at,
            "last_run_at": schedule.last_run_at,
            "last_status": schedule.last_status,
            "consecutive_failures": schedule.consecutive_failures,
            "created_at": schedule.created_at,
            "updated_at": schedule.updated_at,
        }

    def serialize_execution(self, execution: WorkflowExecution) -> dict:
        return {
            "id": execution.id,
            "schedule_id": execution.schedule_id,
            "user_id": execution.user_id,
            "trigger_type": execution.trigger_type,
            "status": execution.status,
            "scheduled_for": execution.scheduled_for,
            "started_at": execution.started_at,
            "completed_at": execution.completed_at,
            "result_summary": execution.result_summary,
            "result_detail": self._json(execution.result_detail_json),
            "error_message": execution.error_message,
            "retry_count": execution.retry_count,
            "created_at": execution.created_at,
            "updated_at": execution.updated_at,
        }

    def create_schedule(self, *, db: Session, user: User, request) -> ScheduledWorkflow:
        if request.workflow_type not in self.WORKFLOW_TYPES:
            raise ValueError("不支持的工作流类型")
        if request.frequency not in self.FREQUENCIES:
            raise ValueError("frequency 仅支持 daily 或 weekly")
        if request.frequency == "weekly" and request.weekday is None:
            raise ValueError("每周计划必须选择 weekday")
        if request.workflow_type == "meeting_followup" and not request.config.get("meeting_id"):
            raise ValueError("会后提醒计划必须选择会议")
        normalized_time = self._validate_time(request.run_time)
        self._zone(request.timezone)
        schedule = ScheduledWorkflow(
            user_id=user.id,
            organization_id=user.organization_id,
            department_id=user.department_id,
            name=request.name.strip(),
            workflow_type=request.workflow_type,
            frequency=request.frequency,
            run_time=normalized_time,
            weekday=request.weekday if request.frequency == "weekly" else None,
            timezone=request.timezone,
            config_json=json.dumps(request.config, ensure_ascii=False),
            enabled=True,
        )
        schedule.next_run_at = self._next_run(schedule)
        db.add(schedule)
        db.commit()
        db.refresh(schedule)
        oplog_service.log(module="scheduler", action="schedule_created", db=db, user_id=user.id, target_type="schedule", target_id=schedule.id, detail=f"workflow_type={schedule.workflow_type}")
        return schedule

    def list_schedules(self, *, db: Session, user: User) -> list[ScheduledWorkflow]:
        query = db.query(ScheduledWorkflow)
        if user.role != "admin":
            query = query.filter(ScheduledWorkflow.user_id == user.id)
        return query.order_by(ScheduledWorkflow.enabled.desc(), ScheduledWorkflow.next_run_at.asc(), ScheduledWorkflow.id.desc()).all()

    def get_schedule(self, schedule_id: int, *, db: Session, user: User) -> ScheduledWorkflow | None:
        schedule = db.query(ScheduledWorkflow).filter(ScheduledWorkflow.id == schedule_id).first()
        return schedule if schedule and self._can_access(schedule, user) else None

    def update_schedule(self, schedule_id: int, *, db: Session, user: User, request) -> ScheduledWorkflow:
        schedule = self.get_schedule(schedule_id, db=db, user=user)
        if not schedule:
            raise ValueError("Schedule not found")
        changes = request.model_dump(exclude_unset=True)
        if "name" in changes:
            schedule.name = changes["name"].strip()
        if "run_time" in changes:
            schedule.run_time = self._validate_time(changes["run_time"])
        if "weekday" in changes and schedule.frequency == "weekly":
            schedule.weekday = changes["weekday"]
        if "config" in changes:
            schedule.config_json = json.dumps(changes["config"], ensure_ascii=False)
        if "enabled" in changes:
            schedule.enabled = changes["enabled"]
        schedule.next_run_at = self._next_run(schedule) if schedule.enabled else None
        db.commit()
        db.refresh(schedule)
        oplog_service.log(module="scheduler", action="schedule_updated", db=db, user_id=user.id, target_type="schedule", target_id=schedule.id, detail=f"enabled={schedule.enabled}")
        return schedule

    def _create_execution(self, *, db: Session, schedule: ScheduledWorkflow, trigger_type: str, scheduled_for: datetime | None, idempotency_key: str) -> WorkflowExecution:
        existing = db.query(WorkflowExecution).filter(WorkflowExecution.idempotency_key == idempotency_key).first()
        if existing:
            return existing
        execution = WorkflowExecution(
            schedule_id=schedule.id,
            user_id=schedule.user_id,
            trigger_type=trigger_type,
            idempotency_key=idempotency_key,
            status="pending",
            scheduled_for=scheduled_for,
        )
        db.add(execution)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return db.query(WorkflowExecution).filter(WorkflowExecution.idempotency_key == idempotency_key).one()
        db.refresh(execution)
        return execution

    def start_manual_run(self, schedule_id: int, *, db: Session, user: User) -> WorkflowExecution:
        schedule = self.get_schedule(schedule_id, db=db, user=user)
        if not schedule:
            raise ValueError("Schedule not found")
        execution = self._create_execution(
            db=db,
            schedule=schedule,
            trigger_type="manual",
            scheduled_for=datetime.now(timezone.utc),
            idempotency_key=f"manual:{schedule.id}:{uuid.uuid4().hex}",
        )
        return execution

    def dispatch_due(self, *, db: Session, now: datetime | None = None) -> list[int]:
        current = now or datetime.now(timezone.utc)
        due = db.query(ScheduledWorkflow).filter(ScheduledWorkflow.enabled.is_(True), ScheduledWorkflow.next_run_at <= current).all()
        execution_ids = []
        for schedule in due:
            scheduled_for = schedule.next_run_at
            key = f"scheduled:{schedule.id}:{scheduled_for.isoformat() if scheduled_for else current.isoformat()}"
            execution = self._create_execution(db=db, schedule=schedule, trigger_type="scheduled", scheduled_for=scheduled_for, idempotency_key=key)
            schedule.next_run_at = self._next_run(schedule, now=current)
            db.commit()
            execution_ids.append(execution.id)
        return execution_ids

    def list_executions(self, *, db: Session, user: User, schedule_id: int | None = None, limit: int = 50) -> list[WorkflowExecution]:
        query = db.query(WorkflowExecution)
        if user.role != "admin":
            query = query.filter(WorkflowExecution.user_id == user.id)
        if schedule_id is not None:
            query = query.filter(WorkflowExecution.schedule_id == schedule_id)
        return query.order_by(WorkflowExecution.created_at.desc(), WorkflowExecution.id.desc()).limit(limit).all()

    def _daily_digest(self, *, db: Session, schedule: ScheduledWorkflow, user: User) -> EmailDraft:
        config = self._json(schedule.config_json)
        since = datetime.now(timezone.utc) - timedelta(days=1)
        query = db.query(MailboxMessage).filter(MailboxMessage.user_id == user.id, MailboxMessage.created_at >= since)
        connector_id = config.get("connector_id")
        if connector_id:
            query = query.filter(MailboxMessage.connector_id == int(connector_id))
        messages = query.order_by(MailboxMessage.importance.desc(), MailboxMessage.received_at.desc()).limit(50).all()
        lines = [f"- [{message.importance}] {message.subject or '无主题'}｜{message.sender or '未知发件人'}｜{message.summary or ''}" for message in messages]
        content = "\n".join(["# 每日邮件摘要", "", "## 过去 24 小时", *(lines or ["- 暂无新邮件。"]), "", "该内容为只读摘要草稿，未发送给任何外部地址。"])
        # 幂等：同一用户当日已建摘要草稿则复用，避免重试/重复执行产生多份草稿
        subject = f"每日邮件摘要｜{datetime.now().date().isoformat()}"
        existing = db.query(EmailDraft).filter(
            EmailDraft.user_id == user.id,
            EmailDraft.generation_type == "daily_mail_digest",
            EmailDraft.subject == subject,
        ).first()
        if existing:
            return existing
        draft = EmailDraft(
            user_id=user.id, organization_id=user.organization_id, department_id=user.department_id,
            subject=subject, recipient=config.get("recipient") or None,
            content=content, purpose="每日邮件摘要", key_points=json.dumps([message.subject for message in messages[:10]], ensure_ascii=False),
            need_action=False, generation_type="daily_mail_digest", tone="professional", status="draft",
            metadata_json=json.dumps({"source_type": "scheduled_daily_mail_digest", "message_ids": [message.id for message in messages], "schedule_id": schedule.id}, ensure_ascii=False),
        )
        db.add(draft); db.commit(); db.refresh(draft)
        return draft

    def _meeting_followup(self, *, db: Session, schedule: ScheduledWorkflow, user: User) -> EmailDraft:
        config = self._json(schedule.config_json)
        meeting_id = int(config.get("meeting_id") or 0)
        if not meeting_id:
            raise ValueError("会后提醒计划需要 meeting_id")
        tasks = db.query(Task).filter(Task.user_id == user.id, Task.source_type == "meeting", Task.source_id == meeting_id, Task.status.in_(["todo", "in_progress"])).all()
        content = "\n".join([f"# 会后待办提醒｜会议 {meeting_id}", "", "## 待推进事项", *([f"- {task.title}" for task in tasks] or ["- 暂无待推进的会议任务。"]), "", "请确认负责人、截止时间和处理进度。"])
        # 幂等：同一用户同一会议的提醒草稿已存在则复用，避免重试重复创建
        subject = f"会后待办提醒｜会议 {meeting_id}"
        existing = db.query(EmailDraft).filter(
            EmailDraft.user_id == user.id,
            EmailDraft.generation_type == "meeting_followup",
            EmailDraft.subject == subject,
        ).first()
        if existing:
            return existing
        draft = EmailDraft(
            user_id=user.id, organization_id=user.organization_id, department_id=user.department_id,
            subject=subject, recipient=config.get("recipient") or None,
            content=content, purpose="会后待办提醒", key_points=json.dumps([task.title for task in tasks], ensure_ascii=False),
            need_action=True, generation_type="meeting_followup", tone="professional", status="draft",
            metadata_json=json.dumps({"source_type": "scheduled_meeting_followup", "meeting_ids": [meeting_id], "task_ids": [task.id for task in tasks], "schedule_id": schedule.id}, ensure_ascii=False),
        )
        db.add(draft); db.commit(); db.refresh(draft)
        return draft

    def execute(self, execution_id: int, *, db: Session) -> WorkflowExecution:
        execution = db.query(WorkflowExecution).filter(WorkflowExecution.id == execution_id).first()
        if not execution:
            raise ValueError("Execution not found")
        if execution.status == "succeeded":
            return execution
        schedule = db.query(ScheduledWorkflow).filter(ScheduledWorkflow.id == execution.schedule_id).first()
        user = db.query(User).filter(User.id == execution.user_id).first()
        if not schedule or not user:
            raise ValueError("Schedule or user not found")
        if execution.trigger_type == "scheduled" and not schedule.enabled:
            execution.status = "skipped"
            execution.completed_at = datetime.now(timezone.utc)
            execution.result_summary = "计划已暂停，跳过本次执行"
            db.commit()
            return execution
        execution.status = "running"; execution.started_at = datetime.now(timezone.utc); db.commit()
        try:
            config = self._json(schedule.config_json)
            if schedule.workflow_type == "daily_mail_digest":
                draft = self._daily_digest(db=db, schedule=schedule, user=user)
            elif schedule.workflow_type == "weekly_report":
                draft = workflow_service.create_weekly_report_draft(db=db, user=user, scope=str(config.get("scope") or "mine"), start_date=None, end_date=None, recipient=config.get("recipient"), title=config.get("title"))
            elif schedule.workflow_type == "meeting_followup":
                draft = self._meeting_followup(db=db, schedule=schedule, user=user)
            else:
                raise ValueError("Unsupported workflow type")
            execution.status = "succeeded"; execution.completed_at = datetime.now(timezone.utc); execution.result_summary = f"已生成草稿 #{draft.id}"; execution.result_detail_json = json.dumps({"draft_id": draft.id, "workflow_type": schedule.workflow_type}, ensure_ascii=False)
            schedule.last_run_at = execution.completed_at; schedule.last_status = "succeeded"; schedule.consecutive_failures = 0
            db.commit(); db.refresh(execution)
            oplog_service.log(module="scheduler", action="schedule_execution_succeeded", db=db, user_id=user.id, target_type="workflow_execution", target_id=execution.id, detail=f"schedule_id={schedule.id}; draft_id={draft.id}")
            return execution
        except Exception as exc:
            execution.status = "failed"; execution.completed_at = datetime.now(timezone.utc); execution.error_message = "计划执行失败，请查看系统日志"; schedule.last_run_at = execution.completed_at; schedule.last_status = "failed"; schedule.consecutive_failures += 1; db.commit()
            oplog_service.log(module="scheduler", action="schedule_execution_failed", db=db, user_id=user.id, target_type="workflow_execution", target_id=execution.id, detail=f"schedule_id={schedule.id}; error=redacted")
            raise exc


scheduler_service = SchedulerService()
