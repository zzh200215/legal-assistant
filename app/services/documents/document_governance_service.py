from __future__ import annotations

import json
from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.models.document import Document, DocumentAccessRule, KnowledgeBase
from app.models.user import User


class DocumentGovernanceService:
    def get_or_create_knowledge_base(
        self,
        *,
        db: Session,
        user_id: int,
        name: str,
        organization_id: int | None = None,
        department_id: int | None = None,
        category: str | None = None,
        description: str | None = None,
        permission_scope: str = "private",
    ) -> KnowledgeBase:
        kb = (
            db.query(KnowledgeBase)
            .filter(KnowledgeBase.user_id == user_id, KnowledgeBase.name == name)
            .first()
        )
        if kb:
            if category is not None:
                kb.category = category
            if description is not None:
                kb.description = description
            kb.permission_scope = permission_scope or kb.permission_scope
            db.add(kb)
            db.commit()
            db.refresh(kb)
            return kb

        kb = KnowledgeBase(
            user_id=user_id,
            organization_id=organization_id,
            department_id=department_id,
            name=name,
            category=category,
            description=description,
            permission_scope=permission_scope or "private",
        )
        db.add(kb)
        db.commit()
        db.refresh(kb)
        return kb

    def list_knowledge_bases(
        self,
        *,
        db: Session,
        user_id: int,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[KnowledgeBase]:
        from app.services.org.authorization_service import authorization_service

        scope_filter = authorization_service.knowledge_base_scope_filter(
            user_id=user_id,
            organization_id=organization_id,
            department_id=department_id,
        )
        return (
            db.query(KnowledgeBase)
            .filter(scope_filter)
            .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
            .all()
        )

    def assign_document_access_rules(
        self,
        *,
        db: Session,
        document_id: int,
        users: Iterable[str] | None = None,
        roles: Iterable[str] | None = None,
    ) -> None:
        db.query(DocumentAccessRule).filter(DocumentAccessRule.document_id == document_id).delete()
        for user_value in users or []:
            db.add(
                DocumentAccessRule(
                    document_id=document_id,
                    subject_type="user",
                    subject_value=str(user_value),
                    permission="read",
                )
            )
        for role_value in roles or []:
            db.add(
                DocumentAccessRule(
                    document_id=document_id,
                    subject_type="role",
                    subject_value=str(role_value),
                    permission="read",
                )
            )
        db.commit()

    def update_document_governance(
        self,
        *,
        db: Session,
        document: Document,
        classification: str | None = None,
        tags: list[str] | None = None,
        permission_scope: str | None = None,
        permission_users: list[str] | None = None,
        permission_roles: list[str] | None = None,
        knowledge_base_id: int | None = None,
        metadata: dict | None = None,
    ) -> Document:
        if classification is not None:
            document.classification = classification
        if tags is not None:
            document.tags = json.dumps(tags, ensure_ascii=False)
        if permission_scope is not None:
            document.permission_scope = permission_scope
        if permission_users is not None:
            document.permission_users = json.dumps(permission_users, ensure_ascii=False)
        if permission_roles is not None:
            document.permission_roles = json.dumps(permission_roles, ensure_ascii=False)
        if knowledge_base_id is not None:
            document.knowledge_base_id = knowledge_base_id
            self._refresh_rag_metadata(document.id, knowledge_base_id=knowledge_base_id)
        if metadata is not None:
            document.metadata_json = json.dumps(metadata, ensure_ascii=False)
        db.add(document)
        db.commit()
        db.refresh(document)
        # 仅在显式传入权限列表时才重建访问规则，避免仅更新分类/标签时清空已有规则
        if permission_users is not None or permission_roles is not None:
            self.assign_document_access_rules(
                db=db,
                document_id=document.id,
                users=permission_users or [],
                roles=permission_roles or [],
            )
        return document

    def can_access_document(
        self,
        *,
        db: Session,
        document: Document,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> bool:
        """文档访问判断（统一委托 AuthorizationService）。"""
        from app.services.org.authorization_service import AuthorizationContext, authorization_service

        ctx = AuthorizationContext(
            user_id=user_id,
            system_role=role,
            organization_id=organization_id,
            department_id=department_id,
            legal_role=role,
        )
        return authorization_service.can_access_document(db=db, ctx=ctx, document=document)

    def find_latest_version(
        self,
        *,
        db: Session,
        user_id: int,
        title: str,
        content_hash: str | None,
    ) -> Document | None:
        query = db.query(Document).filter(Document.user_id == user_id, Document.title == title)
        if content_hash:
            query = query.filter(Document.content_hash == content_hash)
        return query.order_by(Document.version_number.desc(), Document.id.desc()).first()

    def list_sensitive_documents(
        self,
        *,
        db: Session,
        user: User,
        sensitivity_level: str | None = None,
    ) -> list[Document]:
        from app.services.org.authorization_service import authorization_service

        scope_filter = authorization_service.document_scope_filter(
            db,
            user_id=user.id,
            organization_id=user.organization_id,
            department_id=user.department_id,
            role=user.role,
        )
        query = db.query(Document).filter(scope_filter)
        if sensitivity_level:
            query = query.filter(Document.sensitivity_level == sensitivity_level)
        return query.order_by(Document.created_at.desc(), Document.id.desc()).all()

    def list_accessible_document_ids(
        self,
        *,
        db: Session,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[int]:
        """返回当前用户在访问层可见的全部文档 ID（含共享文档），供 RAG 检索作授权上下文。

        使用 SQL 过滤，不读全表。
        """
        from app.services.org.authorization_service import authorization_service

        scope_filter = authorization_service.document_scope_filter(
            db,
            user_id=user_id,
            organization_id=organization_id,
            department_id=department_id,
            role=role,
        )
        rows = (
            db.query(Document.id)
            .filter(scope_filter)
            .order_by(Document.id.asc())
            .all()
        )
        return [row[0] for row in rows]

    @staticmethod
    def _refresh_rag_metadata(document_id: int, *, knowledge_base_id: int) -> None:
        """知识库归属变化时同步 RAG chunk 元数据，避免 knowledge_base_id 过滤失配。"""
        try:
            from app.services.rag.rag_service import rag_service

            rag_service.refresh_document_metadata(document_id, knowledge_base_id=knowledge_base_id)
        except Exception:
            # 向量库不可用不阻断治理更新；下次重解析会重写元数据
            pass

    @staticmethod
    def _json_list(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            return []
        return []


document_governance_service = DocumentGovernanceService()
