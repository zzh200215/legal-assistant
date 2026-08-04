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

    def list_knowledge_bases(self, *, db: Session, user_id: int) -> list[KnowledgeBase]:
        return (
            db.query(KnowledgeBase)
            .filter(KnowledgeBase.user_id == user_id)
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
        if metadata is not None:
            document.metadata_json = json.dumps(metadata, ensure_ascii=False)
        db.add(document)
        db.commit()
        db.refresh(document)
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
        document: Document,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> bool:
        if document.user_id == user_id:
            return True
        scope = (document.permission_scope or "private").strip().lower()
        if scope == "public":
            return True
        if scope == "org":
            return bool(organization_id and document.organization_id and organization_id == document.organization_id)
        if scope == "department":
            return bool(department_id and document.department_id and department_id == document.department_id)
        if scope == "role":
            allowed_roles = self._json_list(document.permission_roles)
            return bool(role and role in allowed_roles)
        if scope == "restricted":
            allowed_users = self._json_list(document.permission_users)
            allowed_roles = self._json_list(document.permission_roles)
            return str(user_id) in allowed_users or bool(role and role in allowed_roles)
        return False

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
        query = db.query(Document)
        if sensitivity_level:
            query = query.filter(Document.sensitivity_level == sensitivity_level)
        rows = query.order_by(Document.created_at.desc(), Document.id.desc()).all()
        return [
            row
            for row in rows
            if self.can_access_document(
                document=row,
                user_id=user.id,
                role=user.role,
                organization_id=user.organization_id,
                department_id=user.department_id,
            )
        ]

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
