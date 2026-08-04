from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.email import EmailDraft
from app.models.meeting import Meeting, MeetingSummary
from app.models.task import Task
from app.models.user import User
from app.services.meeting_service import meeting_service
from app.services.oplog_service import oplog_service
from app.services.task_service import task_service


class WorkflowService:
    """P0 workflow orchestration. All outputs remain internal drafts or confirmed tasks."""

    @staticmethod
    def _load_json(value: str | None) -> list:
        if not value:
            return []
        try:
            data = json.loads(value)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    @staticmethod
    def _normalize_priority(value: str | None) -> str:
        return value if value in {"low", "medium", "high"} else "medium"

    def _get_meeting(self, meeting_id: int, *, db: Session, user: User) -> Meeting:
        meeting = meeting_service.get(
            meeting_id,
            db,
            user_id=user.id,
            role=user.role,
            organization_id=user.organization_id,
            department_id=user.department_id,
        )
        if not meeting:
            raise ValueError("Meeting not found")
        return meeting

    def _meeting_action_items(self, meeting_id: int, *, db: Session, user: User) -> tuple[Meeting, list[dict]]:
        meeting = self._get_meeting(meeting_id, db=db, user=user)
        summary = db.query(MeetingSummary).filter(MeetingSummary.meeting_id == meeting_id).first()
        if not summary:
            raise ValueError("Meeting summary not found")
        raw_items = self._load_json(summary.action_items)
        items: list[dict] = []
        for index, raw in enumerate(raw_items):
            source = raw if isinstance(raw, dict) else {"title": str(raw)}
            title = str(source.get("title") or source.get("task") or "").strip()
            if not title:
                continue
            items.append(
                {
                    "source_index": index,
                    "title": title,
                    "description": str(source.get("description") or "").strip() or None,
                    "assignee": str(source.get("assignee") or "").strip() or None,
                    "due_date": str(source.get("due_date") or source.get("deadline") or "").strip() or None,
                    "priority": self._normalize_priority(source.get("priority")),
                    "confidence": source.get("confidence"),
                    "evidence": str(source.get("evidence") or "").strip() or None,
                }
            )
        return meeting, items

    def preview_meeting_tasks(self, meeting_id: int, *, db: Session, user: User) -> dict:
        meeting, items = self._meeting_action_items(meeting_id, db=db, user=user)
        existing = db.query(Task).filter(Task.source_type == "meeting", Task.source_id == meeting.id).all()
        existing_by_title = {task.title.strip(): task.id for task in existing}
        for item in items:
            task_id = existing_by_title.get(item["title"])
            item["existing_task_id"] = task_id
            item["already_created"] = task_id is not None
        return {"meeting_id": meeting.id, "meeting_title": meeting.title, "items": items}

    def confirm_meeting_tasks(self, meeting_id: int, selections: list, *, db: Session, user: User) -> dict:
        meeting, preview_items = self._meeting_action_items(meeting_id, db=db, user=user)
        by_index = {item["source_index"]: item for item in preview_items}
        existing_titles = {
            task.title.strip()
            for task in db.query(Task).filter(Task.source_type == "meeting", Task.source_id == meeting.id).all()
        }
        created, skipped = [], []
        for selection in selections:
            if not selection.selected or selection.source_index not in by_index:
                continue
            source = by_index[selection.source_index]
            title = (selection.title or source["title"]).strip()
            if not title:
                continue
            if title in existing_titles:
                skipped.append({"source_index": selection.source_index, "title": title, "reason": "already_created"})
                continue
            task = task_service.create(
                title=title,
                user_id=user.id,
                db=db,
                description=(selection.description if selection.description is not None else source["description"]),
                assignee=(selection.assignee if selection.assignee is not None else source["assignee"]),
                due_date=task_service._parse_due_date(selection.due_date or source["due_date"]),
                priority=self._normalize_priority(selection.priority or source["priority"]),
                source_type="meeting",
                source_id=meeting.id,
            )
            existing_titles.add(title)
            created.append(task_service.serialize_task(task))

        oplog_service.log(
            module="workflow",
            action="meeting_tasks_confirmed",
            db=db,
            user_id=user.id,
            target_type="meeting",
            target_id=meeting.id,
            detail=f"created={len(created)}; skipped={len(skipped)}",
        )
        return {"meeting_id": meeting.id, "created_tasks": created, "skipped_items": skipped}

    @staticmethod
    def _report_window(start_date: date | None, end_date: date | None) -> tuple[date, date]:
        end = end_date or date.today()
        start = start_date or (end - timedelta(days=6))
        if start > end:
            raise ValueError("start_date must not be after end_date")
        return start, end

    def create_weekly_report_draft(
        self,
        *,
        db: Session,
        user: User,
        scope: str,
        start_date: date | None,
        end_date: date | None,
        recipient: str | None,
        title: str | None,
    ) -> EmailDraft:
        start, end = self._report_window(start_date, end_date)
        tasks = task_service.list_visible(
            db=db,
            user_id=user.id,
            role=user.role,
            organization_id=user.organization_id,
            department_id=user.department_id,
            scope=scope,
        )
        tasks = [task for task in tasks if task.status != "cancelled"]
        completed = [task for task in tasks if task.status == "done"]
        active = [task for task in tasks if task.status in {"todo", "in_progress"}]
        overdue = [task for task in active if task.due_date and task.due_date.date() < date.today()]
        meeting_ids = sorted({task.source_id for task in tasks if task.source_type == "meeting" and task.source_id})

        def task_line(task: Task) -> str:
            owner = f"（负责人：{task.assignee}）" if task.assignee else ""
            deadline = f"，截止：{task.due_date.date().isoformat()}" if task.due_date else ""
            return f"- {task.title}{owner}{deadline}"

        content = "\n".join(
            [
                f"# 项目周报（{start.isoformat()} 至 {end.isoformat()}）",
                "",
                "## 本周进展",
                *([task_line(task) for task in completed] or ["- 本周期暂无已完成任务记录。"]),
                "",
                "## 待推进事项",
                *([task_line(task) for task in active] or ["- 当前没有待推进任务。"]),
                "",
                "## 风险与关注",
                *([task_line(task) for task in overdue] or ["- 暂无逾期任务。"]),
                "",
                "## 下周计划",
                *([f"- 推进：{task.title}" for task in active[:8]] or ["- 根据新增任务补充下周计划。"]),
            ]
        )
        subject = (title or f"项目周报｜{end.isoformat()}").strip()
        metadata = {
            "source_type": "workflow_weekly_report",
            "task_ids": [task.id for task in tasks],
            "meeting_ids": meeting_ids,
            "task_scope": scope,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        draft = EmailDraft(
            user_id=user.id,
            organization_id=user.organization_id,
            department_id=user.department_id,
            subject=subject,
            recipient=(recipient or "").strip() or None,
            content=content,
            purpose="项目周报",
            key_points=json.dumps([task.title for task in active[:10]], ensure_ascii=False),
            need_action=False,
            generation_type="weekly_report",
            tone="professional",
            status="draft",
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        oplog_service.log(
            module="workflow",
            action="weekly_report_draft_created",
            db=db,
            user_id=user.id,
            target_type="email_draft",
            target_id=draft.id,
            detail=f"task_count={len(tasks)}; meeting_count={len(meeting_ids)}",
        )
        return draft

    def create_risk_followup_draft(self, meeting_id: int, *, db: Session, user: User, recipient: str | None) -> EmailDraft:
        meeting = self._get_meeting(meeting_id, db=db, user=user)
        summary = db.query(MeetingSummary).filter(MeetingSummary.meeting_id == meeting.id).first()
        if not summary:
            raise ValueError("Meeting summary not found")
        risks = self._load_json(summary.risks)
        tasks = db.query(Task).filter(Task.source_type == "meeting", Task.source_id == meeting.id, Task.status != "done").all()
        risk_lines = [str(item.get("title") or item.get("description") or "风险待确认") for item in risks if isinstance(item, dict)]
        content = "\n".join(
            [
                f"# {meeting.title}｜风险待办同步",
                "",
                "## 风险提示",
                *([f"- {line}" for line in risk_lines] or ["- 会议纪要中暂无结构化风险，请结合待办跟进。"]),
                "",
                "## 待跟进事项",
                *([f"- {task.title}" for task in tasks] or ["- 暂无未完成会议任务。"]),
                "",
                "请确认责任人、截止时间和处理进度。",
            ]
        )
        metadata = {
            "source_type": "workflow_risk_followup",
            "meeting_ids": [meeting.id],
            "task_ids": [task.id for task in tasks],
        }
        draft = EmailDraft(
            user_id=user.id,
            organization_id=user.organization_id,
            department_id=user.department_id,
            subject=f"风险待办同步｜{meeting.title}",
            recipient=(recipient or "").strip() or None,
            content=content,
            purpose="风险待办同步",
            key_points=json.dumps(risk_lines, ensure_ascii=False),
            need_action=True,
            generation_type="risk_followup",
            tone="professional",
            status="draft",
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
        oplog_service.log(
            module="workflow",
            action="risk_followup_draft_created",
            db=db,
            user_id=user.id,
            target_type="email_draft",
            target_id=draft.id,
            detail=f"meeting_id={meeting.id}; task_count={len(tasks)}",
        )
        return draft


workflow_service = WorkflowService()
