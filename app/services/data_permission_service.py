"""数据权限隔离服务"""
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from app.models.user import User, UserRole, UserStatus
from app.models.document import Document, KnowledgeBase, DocumentAccessRule
from app.models.task import Task
from app.models.meeting import Meeting


class PermissionScope:
    """权限范围"""
    PRIVATE = "private"  # 仅本人
    DEPARTMENT = "department"  # 部门内
    ORGANIZATION = "organization"  # 组织内
    PUBLIC = "public"  # 全公开


class DataPermissionService:
    """数据权限服务"""

    def can_access_document(self, db: Session, user: User, document: Document) -> bool:
        """检查用户是否可以访问文档"""
        # 1. 创建者可直接访问
        if document.user_id == user.id:
            return True

        # 2. 系统管理员可访问所有
        if user.is_admin:
            return True

        # 3. 部门管理员可访问本部门文档
        if user.is_dept_admin and document.department_id == user.department_id:
            return True

        # 4. 根据权限范围判断
        scope = document.permission_scope
        if scope == PermissionScope.PUBLIC:
            return True

        if scope == PermissionScope.ORGANIZATION:
            if document.organization_id == user.organization_id:
                return True

        if scope == PermissionScope.DEPARTMENT:
            if document.department_id == user.department_id:
                return True

        if scope == PermissionScope.PRIVATE:
            # 非创建者不可访问
            pass

        # 5. 检查显式授权规则
        access_rules = db.query(DocumentAccessRule).filter(
            DocumentAccessRule.document_id == document.id
        ).all()

        for rule in access_rules:
            if rule.subject_type == "user" and rule.subject_value == str(user.id):
                return True
            if rule.subject_type == "role" and rule.subject_value == user.role:
                return True
            if rule.subject_type == "department" and rule.subject_value == str(user.department_id):
                return True
            if rule.subject_type == "organization" and rule.subject_value == str(user.organization_id):
                return True

        return False

    def can_modify_document(self, db: Session, user: User, document: Document) -> bool:
        """检查用户是否可以修改文档"""
        # 创建者可修改
        if document.user_id == user.id:
            return True

        # 系统管理员可修改
        if user.is_admin:
            return True

        # 部门管理员可修改本部门文档
        if user.is_dept_admin and document.department_id == user.department_id:
            return True

        # 检查显式授权是否有 write 权限
        access_rules = db.query(DocumentAccessRule).filter(
            and_(
                DocumentAccessRule.document_id == document.id,
                DocumentAccessRule.permission == "write",
            )
        ).all()

        for rule in access_rules:
            if rule.subject_type == "user" and rule.subject_value == str(user.id):
                return True
            if rule.subject_type == "role" and rule.subject_value == user.role:
                return True

        return False

    def can_access_knowledge_base(self, db: Session, user: User, kb: KnowledgeBase) -> bool:
        """检查用户是否可以访问知识库"""
        if kb.user_id == user.id:
            return True

        if user.is_admin:
            return True

        if user.is_dept_admin and kb.department_id == user.department_id:
            return True

        scope = kb.permission_scope
        if scope == PermissionScope.PUBLIC:
            return True

        if scope == PermissionScope.ORGANIZATION:
            if kb.organization_id == user.organization_id:
                return True

        if scope == PermissionScope.DEPARTMENT:
            if kb.department_id == user.department_id:
                return True

        return False

    def can_access_task(self, db: Session, user: User, task: Task) -> bool:
        """检查用户是否可以访问任务"""
        # 创建者可访问
        if task.user_id == user.id:
            return True

        # 系统管理员可访问
        if user.is_admin:
            return True

        # 部门管理员可访问本部门任务
        if user.is_dept_admin and task.department_id == user.department_id:
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

        if user.is_admin:
            return True

        if user.is_dept_admin and task.department_id == user.department_id:
            return True

        if task.assignee and (task.assignee == user.username or task.assignee == str(user.id)):
            return True

        return False

    def can_access_meeting(self, db: Session, user: User, meeting: Meeting) -> bool:
        """检查用户是否可以访问会议"""
        if meeting.user_id == user.id:
            return True

        if user.is_admin:
            return True

        if user.is_dept_admin and meeting.department_id == user.department_id:
            return True

        # 会议纪要共享范围
        # 可扩展：meeting 表增加 participants 字段

        return False

    def filter_documents_for_user(self, db: Session, user: User, query=None) -> List[Document]:
        """过滤用户可访问的文档"""
        base_query = db.query(Document)

        if user.is_admin:
            return base_query.all()

        conditions = [
            Document.user_id == user.id,  # 自己的
            Document.permission_scope == PermissionScope.PUBLIC,  # 公开的
        ]

        if user.organization_id:
            conditions.append(
                and_(
                    Document.permission_scope == PermissionScope.ORGANIZATION,
                    Document.organization_id == user.organization_id,
                )
            )

        if user.department_id:
            conditions.append(
                and_(
                    Document.permission_scope == PermissionScope.DEPARTMENT,
                    Document.department_id == user.department_id,
                )
            )

        if user.is_dept_admin and user.department_id:
            conditions.append(Document.department_id == user.department_id)

        # 显式授权的文档
        access_rules = db.query(DocumentAccessRule.document_id).filter(
            or_(
                DocumentAccessRule.subject_type == "user",
                DocumentAccessRule.subject_value == str(user.id),
                DocumentAccessRule.subject_type == "role",
                DocumentAccessRule.subject_value == user.role,
                DocumentAccessRule.subject_type == "department",
                DocumentAccessRule.subject_value == str(user.department_id),
                DocumentAccessRule.subject_type == "organization",
                DocumentAccessRule.subject_value == str(user.organization_id),
            )
        ).subquery()

        conditions.append(Document.id.in_(access_rules))

        return base_query.filter(or_(*conditions)).all()

    def filter_tasks_for_user(self, db: Session, user: User) -> List[Task]:
        """过滤用户可访问的任务"""
        base_query = db.query(Task)

        if user.is_admin:
            return base_query.all()

        conditions = [
            Task.user_id == user.id,
        ]

        if user.is_dept_admin and user.department_id:
            conditions.append(Task.department_id == user.department_id)

        # 分配给用户的任务
        conditions.append(Task.assignee == user.username)
        conditions.append(Task.assignee == str(user.id))

        # 协作者
        # SQLite 不支持正则，这里用 like 粗略匹配
        conditions.append(Task.collaborators.like(f"%{user.username}%"))
        conditions.append(Task.collaborators.like(f"%{user.id}%"))

        return base_query.filter(or_(*conditions)).all()

    def filter_meetings_for_user(self, db: Session, user: User) -> List[Meeting]:
        """过滤用户可访问的会议"""
        base_query = db.query(Meeting)

        if user.is_admin:
            return base_query.all()

        conditions = [
            Meeting.user_id == user.id,
        ]

        if user.is_dept_admin and user.department_id:
            conditions.append(Meeting.department_id == user.department_id)

        if user.organization_id:
            conditions.append(Meeting.organization_id == user.organization_id)

        return base_query.filter(or_(*conditions)).all()


data_permission_service = DataPermissionService()