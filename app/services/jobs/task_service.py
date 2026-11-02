import json
from datetime import datetime, timezone

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session

from app.models.task import Task, TaskCollaborator, TaskComment, TaskLog
from app.models.user import User
from app.core.time import utc_now
from app.services.documents.analysis_service import analysis_service
from app.services.documents.case_link import document_case_id

# 任务列表排序白名单：due_* 把无截止日期的任务放最后，避免 NULL 排在最前/最后随数据库而异。
TASK_LIST_SORTS = {
    "created_desc": (Task.created_at.desc(), Task.id.desc()),
    "due_asc": (Task.due_date.is_(None).asc(), Task.due_date.asc(), Task.id.desc()),
    "due_desc": (Task.due_date.is_(None).asc(), Task.due_date.desc(), Task.id.desc()),
}


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
    def _assignee_display(user: User) -> str:
        return (user.full_name or user.username or f"用户 {user.id}").strip()[:128]

    def _resolve_assignee(
        self, db: Session, assignee_user_id: int | None, organization_id: int | None
    ) -> User | None:
        """负责人必须是同组织用户；跨组织指派直接拒绝，避免通知/可见性越界。"""
        if assignee_user_id is None:
            return None
        user = db.get(User, assignee_user_id)
        if user is None:
            raise ValueError("Assignee not found")
        if organization_id and user.organization_id != organization_id:
            raise ValueError("Assignee not in organization")
        return user

    def _resolve_collaborators(
        self, db: Session, collaborator_user_ids: list[int] | None, organization_id: int | None
    ) -> list[User]:
        users: list[User] = []
        seen: set[int] = set()
        for user_id in collaborator_user_ids or []:
            if user_id in seen:
                continue
            seen.add(user_id)
            user = db.get(User, user_id)
            if user is None:
                raise ValueError("Collaborator not found")
            if organization_id and user.organization_id != organization_id:
                raise ValueError("Collaborator not in organization")
            users.append(user)
        return users

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
        assignee_user_id: int | None = None,
        collaborators: list[str] | None = None,
        collaborator_user_ids: list[int] | None = None,
        due_date: datetime | None = None,
        priority: str = "medium",
        progress: int = 0,
        source_type: str | None = None,
        source_id: int | None = None,
        parent_id: int | None = None,
        case_id: int | None = None,
    ) -> Task:
        owner = db.query(User).filter(User.id == user_id).first()
        assignee_user = self._resolve_assignee(
            db, assignee_user_id, owner.organization_id if owner else None
        )
        collaborator_users = self._resolve_collaborators(
            db, collaborator_user_ids, owner.organization_id if owner else None
        )
        collaborator_names = (
            [self._assignee_display(user) for user in collaborator_users]
            if collaborator_users else collaborators
        )
        task = Task(
            user_id=user_id,
            organization_id=owner.organization_id if owner else None,
            department_id=owner.department_id if owner else None,
            case_id=case_id,
            title=title,
            description=description,
            assignee=self._assignee_display(assignee_user) if assignee_user else assignee,
            assignee_user_id=assignee_user.id if assignee_user else None,
            collaborators=self._dump_collaborators(collaborator_names),
            due_date=due_date,
            priority=priority,
            progress=max(0, min(int(progress or 0), 100)),
            source_type=source_type,
            source_id=source_id,
            parent_id=parent_id,
            status="todo",
        )
        db.add(task)
        db.flush()
        for collaborator in collaborator_users:
            db.add(TaskCollaborator(task_id=task.id, user_id=collaborator.id))
        db.commit()
        db.refresh(task)

        log = TaskLog(
            task_id=task.id, user_id=user_id,
            action="created", detail=f"Task created (source: {source_type})",
        )
        db.add(log)
        db.commit()
        self._notify_collaborators_added(db, task, collaborator_users, user_id)
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

    def update(self, task_id: int, db: Session, user_id: int | None = None,
               if_match_version: int | None = None, role: str | None = None,
               organization_id: int | None = None, department_id: int | None = None,
               **kwargs) -> Task:
        task = self.get(
            task_id, db, user_id=user_id, role=role,
            organization_id=organization_id, department_id=department_id,
        )
        if not task:
            raise ValueError("Task not found")
        is_owner = task.user_id == user_id
        is_assignee = task.assignee_user_id is not None and task.assignee_user_id == user_id
        is_collaborator = any(
            link.user_id == user_id for link in task.collaborator_links
        )
        if not (is_owner or is_assignee or is_collaborator):
            raise PermissionError("Task edit forbidden")
        if not is_owner:
            # 负责人/协作者只能推进执行状态/进度；标题、截止日期、负责人、协作者与归档仍由创建人维护。
            forbidden = set(kwargs) - {"status", "progress"}
            if forbidden:
                raise PermissionError("Task edit forbidden")

        # If-Match 前置版本校验：不匹配抛 StaleDataError（全局映射 409），
        # 幂等/版本校验顺序由调用方保证（Idempotency-Key 先于 If-Match）。
        if if_match_version is not None and int(task.version or 0) != if_match_version:
            from sqlalchemy.orm.exc import StaleDataError
            raise StaleDataError(
                f"Task {task_id} version mismatch: expected v{if_match_version}, got v{task.version}"
            )

        old_status = task.status
        old_progress = task.progress
        old_assignee = task.assignee
        collaborator_ids_provided = "collaborator_user_ids" in kwargs
        added_collaborators: list[User] = []
        if collaborator_ids_provided:
            kwargs.pop("collaborators", None)
            old_collaborator_ids = {
                link.user_id for link in task.collaborator_links
            }
            users = self._resolve_collaborators(
                db, kwargs.pop("collaborator_user_ids"), task.organization_id
            )
            added_collaborators = [
                user for user in users if user.id not in old_collaborator_ids
            ]
            new_collaborator_ids = {user.id for user in users}
            old_links = {link.user_id: link for link in task.collaborator_links}
            for user_id, link in old_links.items():
                if user_id not in new_collaborator_ids:
                    task.collaborator_links.remove(link)
            for user in users:
                if user.id not in old_links:
                    task.collaborator_links.append(TaskCollaborator(user_id=user.id))
            task.collaborators = (
                self._dump_collaborators([self._assignee_display(user) for user in users])
                if users else None
            )
        if "assignee_user_id" in kwargs:
            assignee_user = self._resolve_assignee(
                db, kwargs.pop("assignee_user_id"), task.organization_id
            )
            task.assignee_user_id = assignee_user.id if assignee_user else None
            task.assignee = self._assignee_display(assignee_user) if assignee_user else None
        if "assignee" in kwargs:
            value = kwargs.pop("assignee")
            if task.assignee_user_id is not None and value != task.assignee:
                task.assignee_user_id = None
            task.assignee = value
        for key, value in kwargs.items():
            if key == "collaborators":
                new_text = self._dump_collaborators(value)
                if new_text != task.collaborators:
                    task.collaborator_links.clear()
                setattr(task, key, new_text)
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
                user_id=user_id,
                action="status_changed",
                detail=f"{old_status} -> {kwargs['status']}",
            )
            db.add(log)
            db.commit()
        if "progress" in kwargs and kwargs["progress"] is not None and kwargs["progress"] != old_progress:
            log = TaskLog(
                task_id=task.id,
                user_id=user_id,
                action="progress_changed",
                detail=f"{old_progress} -> {task.progress}",
            )
            db.add(log)
            db.commit()
        if task.assignee != old_assignee:
            db.add(TaskLog(
                task_id=task.id,
                user_id=user_id,
                action="assignee_changed",
                detail=f"{old_assignee or '未指派'} -> {task.assignee or '未指派'}",
            ))
            db.commit()

        self._notify_collaborators_added(db, task, added_collaborators, user_id)
        return task

    def _notify_collaborators_added(
        self, db: Session, task: Task, users: list[User], actor_id: int
    ) -> int:
        """协作者被加入时发站内通知；owner/负责人本人不重复通知。"""
        if not task.organization_id or not users:
            return 0
        from app.services.notification.notification_service import notification_service

        actor = db.get(User, actor_id)
        actor_name = self._assignee_display(actor) if actor else "同事"
        created = 0
        for user in users:
            if user.id in (task.user_id, task.assignee_user_id):
                continue
            notification_service.create_multi_channel_notification(
                db=db,
                organization_id=task.organization_id,
                user_id=user.id,
                event_type="workflow",
                title=f"你被加入任务：{task.title}",
                body=f"任务 #{task.id} · 由 {actor_name} 邀请协作",
                channels=["site"],
                case_id=task.case_id,
                reference_type="task",
                reference_id=task.id,
                business_version=task.version,
            )
            created += 1
        return created

    def bulk_reassign(
        self,
        task_ids: list[int],
        db: Session,
        *,
        user_id: int,
        assignee_user_id: int | None,
        organization_id: int | None = None,
    ) -> dict:
        """批量改派：只处理当前用户为 owner 的任务，其余计入 skipped。

        一次请求最多 100 条（API 限制）；同一负责人只解析一次，
        跨组织/无效负责人直接 400，避免部分成功。
        """
        assignee_user = self._resolve_assignee(db, assignee_user_id, organization_id)
        updated = 0
        skipped = 0
        seen: set[int] = set()
        for task_id in task_ids:
            if task_id in seen:
                continue
            seen.add(task_id)
            task = self.get(task_id, db, user_id=user_id, require_owner=True)
            if not task:
                skipped += 1
                continue
            if (
                assignee_user is not None
                and task.organization_id
                and task.organization_id != organization_id
            ):
                skipped += 1
                continue
            old_assignee = task.assignee
            task.assignee_user_id = assignee_user.id if assignee_user else None
            task.assignee = self._assignee_display(assignee_user) if assignee_user else None
            if task.assignee != old_assignee:
                db.add(TaskLog(
                    task_id=task.id,
                    user_id=user_id,
                    action="assignee_changed",
                    detail=f"{old_assignee or '未指派'} -> {task.assignee or '未指派'}",
                ))
            updated += 1
        db.commit()
        return {
            "updated": updated,
            "skipped": skipped,
            "assignee_user_id": assignee_user.id if assignee_user else None,
            "assignee": self._assignee_display(assignee_user) if assignee_user else None,
        }

    def bulk_action(
        self,
        task_ids: list[int],
        db: Session,
        *,
        user_id: int,
        action: str,
        priority: str | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> dict:
        """批量完成/归档/改优先级：按 owner/负责人权限逐条处理，无权限计入 skipped。"""
        if action == "priority" and priority not in ("low", "medium", "high"):
            raise ValueError("Invalid priority")
        updated = 0
        skipped = 0
        seen: set[int] = set()
        for task_id in task_ids:
            if task_id in seen:
                continue
            seen.add(task_id)
            task = self.get(
                task_id, db, user_id=user_id, role=role,
                organization_id=organization_id, department_id=department_id,
            )
            if not task:
                skipped += 1
                continue
            is_owner = task.user_id == user_id
            is_assignee = task.assignee_user_id is not None and task.assignee_user_id == user_id

            if action == "complete":
                if not (is_owner or is_assignee) or task.archived_at is not None:
                    skipped += 1
                    continue
                old_status = task.status
                old_progress = task.progress
                task.status = "done"
                task.progress = 100
                if old_status != "done":
                    db.add(TaskLog(
                        task_id=task.id, user_id=user_id, action="status_changed",
                        detail=f"{old_status} -> done",
                    ))
                if old_progress != 100:
                    db.add(TaskLog(
                        task_id=task.id, user_id=user_id, action="progress_changed",
                        detail=f"{old_progress} -> 100",
                    ))
            elif action == "archive":
                if not is_owner or task.archived_at is not None:
                    skipped += 1
                    continue
                task.archived_at = utc_now()
                db.add(TaskLog(
                    task_id=task.id, user_id=user_id,
                    action="archived", detail="Task archived",
                ))
            elif action == "priority":
                if not is_owner or task.archived_at is not None:
                    skipped += 1
                    continue
                old_priority = task.priority
                task.priority = priority
                if old_priority != priority:
                    db.add(TaskLog(
                        task_id=task.id, user_id=user_id, action="priority_changed",
                        detail=f"{old_priority} -> {priority}",
                    ))
            else:
                skipped += 1
                continue
            updated += 1
        db.commit()
        return {"updated": updated, "skipped": skipped, "action": action}

    def archive(self, task_id: int, db: Session, *, user_id: int) -> Task:
        """软归档：仅 owner 可操作；幂等，已归档再调用不重复写日志。"""
        task = self.get(task_id, db, user_id=user_id, require_owner=True)
        if not task:
            raise ValueError("Task not found")
        if task.archived_at is not None:
            return task
        task.archived_at = utc_now()
        db.add(TaskLog(
            task_id=task.id, user_id=user_id,
            action="archived", detail="Task archived",
        ))
        db.commit()
        db.refresh(task)
        return task

    def unarchive(self, task_id: int, db: Session, *, user_id: int) -> Task:
        """恢复归档：仅 owner 可操作；幂等。"""
        task = self.get(task_id, db, user_id=user_id, require_owner=True)
        if not task:
            raise ValueError("Task not found")
        if task.archived_at is None:
            return task
        task.archived_at = None
        db.add(TaskLog(
            task_id=task.id, user_id=user_id,
            action="unarchived", detail="Task unarchived",
        ))
        db.commit()
        db.refresh(task)
        return task

    def create_from_action_items(
        self,
        action_items: list[dict],
        user_id: int,
        source_id: int | None,
        db: Session,
        source_type: str = "meeting",
        case_id: int | None = None,
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
                case_id=case_id,
            )
            tasks.append(task)
        return tasks

    async def extract_from_document(self, document_id: int, user_id: int, db: Session) -> list[Task]:
        from app.services.documents.document_service import document_service

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
            case_id=document_case_id(db, document_id),
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
                case_id=task.case_id,
            )
            sub_tasks.append(sub)

        if task.status == "todo":
            task.status = "in_progress"
            log = TaskLog(
                task_id=task.id, user_id=user_id,
                action="status_changed", detail="todo -> in_progress (decomposed)",
            )
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
                assignee_user_id=task.assignee_user_id,
                collaborator_ids={link.user_id for link in task.collaborator_links},
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
        assignee_user_id: int | None = None,
        collaborator_ids: set[int] | None = None,
    ) -> bool:
        normalized_scope = (scope or "all").strip().lower()
        is_mine = owner_user_id == user_id or (
            assignee_user_id is not None and assignee_user_id == user_id
        )
        is_collaborator = user_id in (collaborator_ids or set())
        is_participating = is_mine or is_collaborator
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
        if normalized_scope == "participating":
            return is_participating
        if normalized_scope == "department":
            return is_same_department
        if normalized_scope == "organization":
            return is_same_organization
        if normalized_scope == "shared":
            return is_same_department or is_same_organization
        return True

    @staticmethod
    def _visible_filter(
        *,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        scope: str | None = None,
    ):
        """把 list_visible 的可见性语义（_can_access_task + _match_scope）下沉为 SQL 条件，
        供分页查询使用（禁止全量加载后内存分页）。"""
        from sqlalchemy import or_

        access = True if role == "admin" else or_(
            Task.user_id == user_id,
            Task.department_id == department_id,
            Task.organization_id == organization_id,
        )
        normalized_scope = (scope or "all").strip().lower()
        collaborator_exists = exists().where(
            TaskCollaborator.task_id == Task.id,
            TaskCollaborator.user_id == user_id,
        )
        if normalized_scope == "mine":
            # 「我的」= 我创建的 + 指派给我的；负责人可以在自己的列表里直接找到任务。
            scope_cond = or_(Task.user_id == user_id, Task.assignee_user_id == user_id)
        elif normalized_scope == "participating":
            # 「我参与的」= 我创建的 + 指派给我的 + 我作为协作者的。
            scope_cond = or_(
                Task.user_id == user_id,
                Task.assignee_user_id == user_id,
                collaborator_exists,
            )
        elif normalized_scope == "department":
            scope_cond = Task.department_id == department_id
        elif normalized_scope == "organization":
            scope_cond = and_(
                Task.organization_id == organization_id,
                Task.user_id != user_id,
                Task.department_id != department_id,
            )
        elif normalized_scope == "shared":
            scope_cond = and_(
                or_(
                    Task.department_id == department_id,
                    Task.organization_id == organization_id,
                ),
                Task.user_id != user_id,
                or_(Task.assignee_user_id.is_(None), Task.assignee_user_id != user_id),
                ~collaborator_exists,
            )
        else:
            scope_cond = True
        return and_(access, scope_cond)

    def _filtered_query(
        self,
        *,
        db: Session,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        status: str | None = None,
        overdue: bool = False,
        overdue_cutoff: datetime | None = None,
        archived: bool = False,
        scope: str | None = None,
        source_type: str | None = None,
        source_id: int | None = None,
        case_id: int | None = None,
        keyword: str | None = None,
    ):
        """列表/统计共用的过滤查询：可见性 + 业务筛选全部下沉 SQL。"""
        cond = self._visible_filter(
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
            scope=scope,
        )
        q = db.query(Task).filter(cond)
        if archived:
            q = q.filter(Task.archived_at.isnot(None))
        else:
            q = q.filter(Task.archived_at.is_(None))
        if status:
            q = q.filter(Task.status == status)
        if overdue:
            cutoff = overdue_cutoff or self._overdue_cutoff(
                db, user_id=user_id, organization_id=organization_id
            )
            q = q.filter(
                Task.due_date.isnot(None),
                Task.status.in_(("todo", "in_progress")),
                Task.due_date < cutoff,
            )
        if source_type:
            q = q.filter(Task.source_type == source_type)
        if source_id is not None:
            q = q.filter(Task.source_id == source_id)
        if case_id is not None:
            q = q.filter(Task.case_id == case_id)
        search = (keyword or "").strip()
        if search:
            q = q.filter(
                or_(
                    Task.title.icontains(search, autoescape=True),
                    Task.description.icontains(search, autoescape=True),
                )
            )
        return q

    @staticmethod
    def _overdue_cutoff(
        db: Session, *, user_id: int, organization_id: int | None
    ) -> datetime:
        """按当前用户通知偏好时区取「现在」的 naive 墙上时间，与任务截止日期同口径。"""
        from zoneinfo import ZoneInfo

        from app.core.time import utc_now
        from app.services.notification.notification_service import notification_service

        tz_name = "Asia/Shanghai"
        if organization_id:
            pref = notification_service.preference_for(
                db=db,
                user_id=user_id,
                organization_id=organization_id,
                event_type="deadline",
            )
            tz_name = (pref.timezone if pref and pref.timezone else tz_name)
        try:
            zone = ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001 - 时区非法按默认时区
            zone = ZoneInfo("Asia/Shanghai")
        return utc_now().astimezone(zone).replace(tzinfo=None)

    def _list_stats(
        self,
        q,
        *,
        total: int,
        cutoff: datetime,
        user_id: int,
    ) -> dict[str, int]:
        """统计口径与列表过滤一致，但不分页：统计卡不再只算当前页。"""
        return {
            "total": total,
            "in_progress": q.filter(Task.status == "in_progress").count(),
            "overdue": q.filter(
                Task.due_date.isnot(None),
                Task.status.in_(("todo", "in_progress")),
                Task.due_date < cutoff,
            ).count(),
            "shared": q.filter(
                Task.user_id != user_id,
                or_(Task.assignee_user_id.is_(None), Task.assignee_user_id != user_id),
                ~exists().where(
                    TaskCollaborator.task_id == Task.id,
                    TaskCollaborator.user_id == user_id,
                ),
            ).count(),
            "participating": q.filter(
                or_(
                    Task.user_id == user_id,
                    Task.assignee_user_id == user_id,
                    exists().where(
                        TaskCollaborator.task_id == Task.id,
                        TaskCollaborator.user_id == user_id,
                    ),
                )
            ).count(),
        }

    def paginate_visible(
        self,
        *,
        db: Session,
        user_id: int,
        page: int,
        page_size: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        status: str | None = None,
        overdue: bool = False,
        archived: bool = False,
        scope: str | None = None,
        source_type: str | None = None,
        source_id: int | None = None,
        case_id: int | None = None,
        keyword: str | None = None,
        sort: str = "created_desc",
    ) -> tuple[list[Task], int, dict[str, int]]:
        """数据库分页版 list_visible：可见性过滤全部下沉 SQL，count 走 DB。"""
        cutoff = self._overdue_cutoff(
            db, user_id=user_id, organization_id=organization_id
        )
        q = self._filtered_query(
            db=db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
            status=status,
            overdue=overdue,
            overdue_cutoff=cutoff,
            archived=archived,
            scope=scope,
            source_type=source_type,
            source_id=source_id,
            case_id=case_id,
            keyword=keyword,
        )
        total = q.count()
        stats = self._list_stats(
            q,
            total=total,
            cutoff=cutoff,
            user_id=user_id,
        )
        order_by = TASK_LIST_SORTS.get(sort, TASK_LIST_SORTS["created_desc"])
        items = (
            q.order_by(*order_by)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total, stats

    def paginate_sub_tasks(
        self,
        task_id: int,
        *,
        db: Session,
        user_id: int,
        page: int,
        page_size: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> tuple[list[Task], int]:
        """子任务 DB 分页：可见性（admin/本人/本部门/本组织）下沉 SQL。"""
        cond = and_(
            Task.parent_id == task_id,
            self._visible_filter(
                user_id=user_id,
                role=role,
                organization_id=organization_id,
                department_id=department_id,
            ),
        )
        q = db.query(Task).filter(cond)
        total = q.count()
        items = (
            q.order_by(Task.created_at.desc(), Task.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return items, total

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

    def add_comment(
        self,
        task_id: int,
        user_id: int,
        content: str,
        db: Session,
        *,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> TaskComment:
        task = self.get(
            task_id, db, user_id=user_id, role=role,
            organization_id=organization_id, department_id=department_id,
        )
        if not task:
            raise ValueError("Task not found")
        if (
            task.user_id != user_id
            and task.assignee_user_id != user_id
            and not any(link.user_id == user_id for link in task.collaborator_links)
        ):
            raise PermissionError("Task comment forbidden")
        normalized = (content or "").strip()
        if not normalized:
            raise ValueError("Comment content is empty")
        comment = TaskComment(task_id=task_id, user_id=user_id, content=normalized)
        db.add(comment)
        db.commit()
        db.refresh(comment)
        log = TaskLog(
            task_id=task_id, user_id=user_id,
            action="comment_added", detail=normalized[:120],
        )
        db.add(log)
        db.commit()
        return comment

    def update_comment(
        self,
        task_id: int,
        comment_id: int,
        content: str,
        db: Session,
        *,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> TaskComment:
        task = self.get(
            task_id, db, user_id=user_id, role=role,
            organization_id=organization_id, department_id=department_id,
        )
        if not task:
            raise ValueError("Task not found")
        comment = db.query(TaskComment).filter(
            TaskComment.id == comment_id, TaskComment.task_id == task_id
        ).first()
        if not comment:
            raise ValueError("Comment not found")
        if comment.user_id != user_id:
            raise PermissionError("Task comment forbidden")
        normalized = (content or "").strip()
        if not normalized:
            raise ValueError("Comment content is empty")
        comment.content = normalized
        db.add(TaskLog(
            task_id=task_id, user_id=user_id,
            action="comment_updated", detail=normalized[:120],
        ))
        db.commit()
        db.refresh(comment)
        return comment

    def delete_comment(
        self,
        task_id: int,
        comment_id: int,
        db: Session,
        *,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> None:
        task = self.get(
            task_id, db, user_id=user_id, role=role,
            organization_id=organization_id, department_id=department_id,
        )
        if not task:
            raise ValueError("Task not found")
        comment = db.query(TaskComment).filter(
            TaskComment.id == comment_id, TaskComment.task_id == task_id
        ).first()
        if not comment:
            raise ValueError("Comment not found")
        if comment.user_id != user_id and task.user_id != user_id:
            raise PermissionError("Task comment forbidden")
        db.delete(comment)
        db.add(TaskLog(
            task_id=task_id, user_id=user_id,
            action="comment_deleted", detail=f"comment #{comment_id}",
        ))
        db.commit()

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
            "case_id": getattr(task, "case_id", None),
            "title": task.title,
            "description": task.description,
            "assignee": task.assignee,
            "assignee_user_id": getattr(task, "assignee_user_id", None),
            "collaborators": self._load_collaborators(task.collaborators),
            "collaborator_user_ids": [
                link.user_id for link in getattr(task, "collaborator_links", [])
            ],
            "status": task.status,
            "priority": task.priority,
            "progress": task.progress,
            "due_date": task.due_date,
            "archived_at": getattr(task, "archived_at", None),
            "source_type": task.source_type,
            "source_id": task.source_id,
            "parent_id": task.parent_id,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }


task_service = TaskService()
