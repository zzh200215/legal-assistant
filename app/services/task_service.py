import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.task import Task, TaskComment, TaskLog
from app.models.user import User
from app.services.analysis_service import analysis_service


class TaskService:
    @staticmethod
    def _can_access_task(
        task: Task,
        *,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> bool:
        if role == "admin":
            return True
        if task.user_id == user_id:
            return True
        if department_id and task.department_id and department_id == task.department_id:
            return True
        if organization_id and task.organization_id and organization_id == task.organization_id:
            return True
        return False

    @staticmethod
    def _dump_collaborators(value: list[str] | None) -> str | None:
        if not value:
            return None
        return json.dumps([item.strip() for item in value if item and item.strip()], ensure_ascii=False)

    @staticmethod
    def _load_collaborators(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            data = json.loads(value)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    @staticmethod
    def _parse_due_date(value: str | None) -> datetime | None:
        if not value:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        try:
            if len(normalized) == 10:
                return datetime.fromisoformat(f"{normalized}T00:00:00")
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def create(
        self,
        title: str,
        user_id: int,
        db: Session,
        description: str | None = None,
        assignee: str | None = None,
        collaborators: list[str] | None = None,
        due_date: datetime | None = None,
        priority: str = "medium",
        progress: int = 0,
        source_type: str | None = None,
        source_id: int | None = None,
        parent_id: int | None = None,
    ) -> Task:
        owner = db.query(User).filter(User.id == user_id).first()
        task = Task(
            user_id=user_id,
            organization_id=owner.organization_id if owner else None,
            department_id=owner.department_id if owner else None,
            title=title,
            description=description,
            assignee=assignee,
            collaborators=self._dump_collaborators(collaborators),
            due_date=due_date,
            priority=priority,
            progress=max(0, min(int(progress or 0), 100)),
            source_type=source_type,
            source_id=source_id,
            parent_id=parent_id,
            status="todo",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        log = TaskLog(task_id=task.id, action="created", detail=f"Task created (source: {source_type})")
        db.add(log)
        db.commit()
        return task

    def get(
        self,
        task_id: int,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        require_owner: bool = False,
    ) -> Task | None:
        query = db.query(Task).filter(Task.id == task_id)
        task = query.first()
        if not task:
            return None
        if user_id is None:
            return task
        if require_owner:
            return task if task.user_id == user_id else None
        return task if self._can_access_task(
            task,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        ) else None

    def update(self, task_id: int, db: Session, user_id: int | None = None, **kwargs) -> Task:
        task = self.get(task_id, db, user_id=user_id, require_owner=True)
        if not task:
            raise ValueError("Task not found")

        old_status = task.status
        old_progress = task.progress
        for key, value in kwargs.items():
            if key == "collaborators":
                setattr(task, key, self._dump_collaborators(value))
                continue
            if key == "progress" and value is not None:
                setattr(task, key, max(0, min(int(value), 100)))
                continue
            if hasattr(task, key):
                setattr(task, key, value)
        db.commit()
        db.refresh(task)

        if "status" in kwargs and kwargs["status"] != old_status:
            log = TaskLog(
                task_id=task.id,
                action="status_changed",
                detail=f"{old_status} -> {kwargs['status']}",
            )
            db.add(log)
            db.commit()
        if "progress" in kwargs and kwargs["progress"] is not None and kwargs["progress"] != old_progress:
            log = TaskLog(
                task_id=task.id,
                action="progress_changed",
                detail=f"{old_progress} -> {task.progress}",
            )
            db.add(log)
            db.commit()

        return task

    def create_from_action_items(
        self,
        action_items: list[dict],
        user_id: int,
        source_id: int | None,
        db: Session,
        source_type: str = "meeting",
    ) -> list[Task]:
        tasks = []
        for item in action_items:
            title = item.get("task", item.get("title", "未命名任务"))
            assignee = item.get("assignee")
            deadline_str = item.get("deadline") or item.get("due_date") or ""
            priority = item.get("priority", "medium")

            description_parts = []
            if assignee:
                description_parts.append(f"负责人：{assignee}")
            if deadline_str:
                description_parts.append(f"截止时间：{deadline_str}")
            if item.get("source_text"):
                description_parts.append(f"原文依据：{item['source_text']}")
            if item.get("evidence"):
                description_parts.append(f"原文依据：{item['evidence']}")
            if item.get("description"):
                description_parts.append(f"详细描述：{item['description']}")
            if item.get("confidence") is not None:
                description_parts.append(f"识别置信度：{item['confidence']}")

            task = self.create(
                title=title,
                user_id=user_id,
                db=db,
                description="\n".join(description_parts) if description_parts else None,
                assignee=assignee,
                due_date=self._parse_due_date(deadline_str),
                priority=priority,
                progress=0,
                source_type=source_type,
                source_id=source_id,
            )
            tasks.append(task)
        return tasks

    async def extract_from_document(self, document_id: int, user_id: int, db: Session) -> list[Task]:
        from app.services.document_service import document_service

        raw_text = document_service._get_document_text(document_id, db, user_id=user_id)
        todos = await analysis_service.extract_document_todos(raw_text, user_id=user_id)
        if not todos:
            return []
        return self.create_from_action_items(
            action_items=todos,
            user_id=user_id,
            source_id=document_id,
            db=db,
            source_type="document",
        )

    async def extract_from_chat(self, message: str, user_id: int, db: Session) -> list[Task]:
        items = await analysis_service.extract_tasks_from_chat(message, user_id=user_id)
        if not items:
            return []
        return self.create_from_action_items(
            action_items=items,
            user_id=user_id,
            source_id=None,
            db=db,
            source_type="chat",
        )

    async def decompose(self, task_id: int, user_id: int, db: Session) -> list[Task]:
        task = self.get(task_id, db, user_id=user_id)
        if not task:
            raise ValueError("Task not found")

        items = await analysis_service.decompose_task(task.title, task.description, user_id=user_id)
        if not items:
            return []

        sub_tasks = []
        for item in items:
            sub = self.create(
                title=item.get("title", "子任务"),
                user_id=user_id,
                db=db,
                description=item.get("description"),
                assignee=item.get("assignee"),
                priority=item.get("priority", "medium"),
                progress=0,
                source_type="decompose",
                source_id=task_id,
                parent_id=task_id,
            )
            sub_tasks.append(sub)

        if task.status == "todo":
            task.status = "in_progress"
            log = TaskLog(task_id=task.id, action="status_changed", detail="todo -> in_progress (decomposed)")
            db.add(log)
            db.commit()

        return sub_tasks

    def get_sub_tasks(self, task_id: int, db: Session, user_id: int | None = None) -> list[Task]:
        query = db.query(Task).filter(Task.parent_id == task_id)
        if user_id is not None:
            query = query.filter(Task.user_id == user_id)
        return query.all()

    def list_for_sync(
        self,
        user_id: int,
        db: Session,
        *,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        scope: str = "mine",
        task_ids: list[int] | None = None,
        include_overdue_only: bool = False,
    ) -> list[Task]:
        tasks = self.list_visible(
            db=db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
            scope=scope,
        )
        tasks = [task for task in tasks if task.status in ["todo", "in_progress"]]
        if task_ids:
            task_id_set = set(task_ids)
            tasks = [task for task in tasks if task.id in task_id_set]
        if include_overdue_only:
            now = datetime.now(timezone.utc)
            tasks = [
                task for task in tasks
                if task.due_date is not None and (
                    task.due_date < now if task.due_date.tzinfo else task.due_date.replace(tzinfo=timezone.utc) < now
                )
            ]

        priority_rank = {"high": 0, "medium": 1, "low": 2}
        status_rank = {"todo": 0, "in_progress": 1}

        def sort_key(task: Task) -> tuple[int, int, str, float]:
            due_date = task.due_date.isoformat() if task.due_date else "9999-12-31T23:59:59"
            created_at = task.created_at.timestamp() if task.created_at else 0.0
            return (
                priority_rank.get(task.priority, 9),
                status_rank.get(task.status, 9),
                due_date,
                -created_at,
            )

        return sorted(tasks, key=sort_key)

    def build_sync_email_points(self, tasks: list[Task]) -> list[str]:
        points: list[str] = []
        for task in tasks:
            segments = [task.title]
            status_label = "进行中" if task.status == "in_progress" else "待办"
            segments.append(f"状态：{status_label}")
            if task.assignee:
                segments.append(f"负责人：{task.assignee}")
            if task.progress:
                segments.append(f"进度：{task.progress}%")
            if task.due_date:
                segments.append(f"截止：{task.due_date.date().isoformat()}")
            if task.priority:
                priority_label = {"high": "高", "medium": "中", "low": "低"}.get(task.priority, task.priority)
                segments.append(f"优先级：{priority_label}")
            if task.description:
                compact_description = " ".join(task.description.split())
                segments.append(f"说明：{compact_description[:80]}")
            points.append("；".join(segments))
        return points

    def list_visible(
        self,
        *,
        db: Session,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        status: str | None = None,
        scope: str | None = None,
        source_type: str | None = None,
        source_id: int | None = None,
    ) -> list[Task]:
        rows = db.query(Task).order_by(Task.created_at.desc()).all()
        result = []
        for task in rows:
            if status and task.status != status:
                continue
            if source_type and task.source_type != source_type:
                continue
            if source_id is not None and task.source_id != source_id:
                continue
            if self._can_access_task(
                task,
                user_id=user_id,
                role=role,
                organization_id=organization_id,
                department_id=department_id,
            ) and self._match_scope(
                task.user_id,
                task.organization_id,
                task.department_id,
                user_id=user_id,
                organization_id=organization_id,
                department_id=department_id,
                scope=scope,
            ):
                result.append(task)
        return result

    @staticmethod
    def _match_scope(
        owner_user_id: int | None,
        owner_organization_id: int | None,
        owner_department_id: int | None,
        *,
        user_id: int,
        organization_id: int | None = None,
        department_id: int | None = None,
        scope: str | None = None,
    ) -> bool:
        normalized_scope = (scope or "all").strip().lower()
        is_mine = owner_user_id == user_id
        is_same_department = bool(
            not is_mine
            and department_id
            and owner_department_id
            and department_id == owner_department_id
        )
        is_same_organization = bool(
            not is_mine
            and not is_same_department
            and organization_id
            and owner_organization_id
            and organization_id == owner_organization_id
        )

        if normalized_scope == "all":
            return True
        if normalized_scope == "mine":
            return is_mine
        if normalized_scope == "department":
            return is_same_department
        if normalized_scope == "organization":
            return is_same_organization
        if normalized_scope == "shared":
            return is_same_department or is_same_organization
        return True

    def list_comments(
        self,
        task_id: int,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[TaskComment]:
        task = self.get(
            task_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not task:
            raise ValueError("Task not found")
        return (
            db.query(TaskComment)
            .filter(TaskComment.task_id == task_id)
            .order_by(TaskComment.created_at.asc(), TaskComment.id.asc())
            .all()
        )

    def add_comment(self, task_id: int, user_id: int, content: str, db: Session) -> TaskComment:
        task = self.get(task_id, db, user_id=user_id, require_owner=True)
        if not task:
            raise ValueError("Task not found")
        normalized = (content or "").strip()
        if not normalized:
            raise ValueError("Comment content is empty")
        comment = TaskComment(task_id=task_id, user_id=user_id, content=normalized)
        db.add(comment)
        db.commit()
        db.refresh(comment)
        log = TaskLog(task_id=task_id, action="comment_added", detail=normalized[:120])
        db.add(log)
        db.commit()
        return comment

    def list_logs(
        self,
        task_id: int,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[TaskLog]:
        task = self.get(
            task_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not task:
            raise ValueError("Task not found")
        return (
            db.query(TaskLog)
            .filter(TaskLog.task_id == task_id)
            .order_by(TaskLog.created_at.desc(), TaskLog.id.desc())
            .all()
        )

    def serialize_task(self, task: Task) -> dict:
        return {
            "id": task.id,
            "user_id": task.user_id,
            "organization_id": task.organization_id,
            "department_id": task.department_id,
            "title": task.title,
            "description": task.description,
            "assignee": task.assignee,
            "collaborators": self._load_collaborators(task.collaborators),
            "status": task.status,
            "priority": task.priority,
            "progress": task.progress,
            "due_date": task.due_date,
            "source_type": task.source_type,
            "source_id": task.source_id,
            "parent_id": task.parent_id,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }


task_service = TaskService()
