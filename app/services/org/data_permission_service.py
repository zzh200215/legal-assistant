"""数据权限隔离服务（统一委托 AuthorizationService，不再保留重复权限算法）。"""
from typing import List
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.document import Document, KnowledgeBase
from app.models.task import Task


class PermissionScope:
    """权限范围"""
    PRIVATE = "private"  # 仅本人
    DEPARTMENT = "department"  # 部门内
    ORGANIZATION = "organization"  # 组织内
    PUBLIC = "public"  # 全公开


class DataPermissionService:
    """数据权限服务（兼容方法统一委托 AuthorizationService）。"""

    def can_access_document(self, db: Session, user: User, document: Document) -> bool:
        """检查用户是否可以访问文档（委托统一服务）。"""
        from app.services.org.authorization_service import authorization_service

        ctx = authorization_service.build_context(db, user)
        return authorization_service.can_access_document(db, ctx, document)

    def can_modify_document(self, db: Session, user: User, document: Document) -> bool:
        """检查用户是否可以修改文档（委托统一服务，仅 owner 或显式 write 授权）。"""
        from app.services.org.authorization_service import authorization_service

        ctx = authorization_service.build_context(db, user)
        return authorization_service.can_access_document(db, ctx, document, write=True)

    def can_access_knowledge_base(self, db: Session, user: User, kb: KnowledgeBase) -> bool:
        """检查用户是否可以访问知识库（委托统一服务）。"""
        from app.services.org.authorization_service import authorization_service

        ctx = authorization_service.build_context(db, user)
        return authorization_service.can_access_knowledge_base(db, ctx, kb)

    def can_access_task(self, db: Session, user: User, task: Task) -> bool:
        """检查用户是否可以访问任务"""
        # 创建者可访问
        if task.user_id == user.id:
            return True

        # 分配给该用户的任务
        if task.assignee and (task.assignee == user.username or task.assignee == str(user.id)):
            return True

        # 协作者
        if task.collaborators:
            collaborators = task.collaborators.split(",")
            if user.username in collaborators or str(user.id) in collaborators:
                return True

        return False

    def can_modify_task(self, db: Session, user: User, task: Task) -> bool:
        """检查用户是否可以修改任务"""
        if task.user_id == user.id:
            return True

        if task.assignee and (task.assignee == user.username or task.assignee == str(user.id)):
            return True

        return False

    def filter_documents_for_user(self, db: Session, user: User, query=None) -> List[Document]:
        """过滤用户可访问的文档（SQL 条件过滤，不读全表）。"""
        from app.services.org.authorization_service import authorization_service

        scope_filter = authorization_service.document_scope_filter(
            db,
            user_id=user.id,
            organization_id=user.organization_id,
            department_id=user.department_id,
            role=user.role,
        )
        base = query if query is not None else db.query(Document)
        return base.filter(scope_filter).order_by(
            Document.created_at.desc(), Document.id.desc()
        ).all()

    def filter_tasks_for_user(self, db: Session, user: User) -> List[Task]:
        """过滤用户可访问的任务"""
        base_query = db.query(Task)

        conditions = [
            Task.user_id == user.id,
        ]

        # 分配给用户的任务
        conditions.append(Task.assignee == user.username)
        conditions.append(Task.assignee == str(user.id))

        # 协作者
        # SQLite 不支持正则，这里用 like 粗略匹配
        conditions.append(Task.collaborators.like(f"%{user.username}%"))
        conditions.append(Task.collaborators.like(f"%{user.id}%"))

        return base_query.filter(or_(*conditions)).all()


data_permission_service = DataPermissionService()
