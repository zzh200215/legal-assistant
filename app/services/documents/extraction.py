from sqlalchemy.orm import Session

from app.services.documents.analysis_service import analysis_service


class ExtractionMixin:
    async def extract_risks(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[dict]:
        raw_text = self._get_document_text(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        return await analysis_service.extract_document_risks(raw_text, user_id=user_id)

    async def extract_todos(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[dict]:
        raw_text = self._get_document_text(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        return await analysis_service.extract_document_todos(raw_text, user_id=user_id)

    async def extract_key_clauses(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[dict]:
        raw_text = self._get_document_text(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        return await analysis_service.extract_document_clauses(raw_text, user_id=user_id)
