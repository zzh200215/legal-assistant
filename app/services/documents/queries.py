from sqlalchemy.orm import Session

from app.models.document import Document, DocumentChunk
from app.services.documents.document_parsing import _extract_text
from app.services.documents.document_pipeline import resolve_local_path


class QueriesMixin:
    def _get_document_text(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> str:
        doc = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not doc:
            raise ValueError("Document not found")
        return _extract_text(str(resolve_local_path(doc)), doc.file_type)

    def get_chunks(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        limit: int = 8,
    ) -> list[DocumentChunk]:
        doc = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not doc:
            raise ValueError("Document not found")
        return (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
            .limit(limit)
            .all()
        )

    def list_documents(
        self,
        *,
        db: Session,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        knowledge_base_id: int | None = None,
        classification: str | None = None,
        sensitivity_level: str | None = None,
        connector_id: int | None = None,
        query: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[Document] | tuple[list[Document], int]:
        """文档列表。page/page_size 同时提供时走 DB offset/limit + count，
        返回 ``(items, total)``；否则返回全量 list（兼容既有调用）。
        connector_id（metadata JSON 子串）与标题检索均下沉 SQL，
        杜绝内存分页与全量加载。
        """
        from sqlalchemy import or_

        from app.services.org.authorization_service import authorization_service

        scope_filter = authorization_service.document_scope_filter(
            db,
            user_id=user_id,
            organization_id=organization_id,
            department_id=department_id,
            role=role,
        )
        q = db.query(Document).filter(scope_filter)
        if knowledge_base_id is not None:
            q = q.filter(Document.knowledge_base_id == knowledge_base_id)
        if classification:
            q = q.filter(Document.classification == classification)
        if sensitivity_level:
            q = q.filter(Document.sensitivity_level == sensitivity_level)
        if connector_id is not None:
            # metadata_json 内 JSON 序列化形如 "connector_id": N，用带后缀子串精确匹配，
            # 避免 1 误匹配 11/123。
            q = q.filter(or_(
                Document.metadata_json.like(f'%"connector_id": {connector_id},%'),
                Document.metadata_json.like(f'%"connector_id": {connector_id}}}%'),
                Document.metadata_json.like(f'%"connector_id": "{connector_id}"%'),
            ))
        if query:
            escaped = (query or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            q = q.filter(Document.title.like(f"%{escaped}%", escape="\\"))
        ordered = q.order_by(Document.created_at.desc(), Document.id.desc())
        if page is not None and page_size is not None:
            total = q.count()
            rows = ordered.offset((page - 1) * page_size).limit(page_size).all()
            return rows, total
        return ordered.all()

    def _build_references(
        self,
        risks: list[dict],
        todos: list[dict],
        clauses: list[dict],
        structured_fields: dict,
        chunks: list[DocumentChunk],
    ) -> list[dict]:
        references = []
        seen = set()

        def add_reference(text: str | None, source_type: str, label: str) -> None:
            normalized = (text or "").strip()
            if not normalized:
                return
            key = normalized[:180]
            if key in seen:
                return
            seen.add(key)
            references.append(
                {
                    "source_type": source_type,
                    "label": label,
                    "quote": normalized[:240],
                }
            )

        for index, item in enumerate(risks, start=1):
            add_reference(item.get("evidence"), "risk", f"风险依据 {index}")
        for index, item in enumerate(todos, start=1):
            add_reference(item.get("source_text") or item.get("evidence"), "todo", f"待办依据 {index}")
        for index, item in enumerate(clauses, start=1):
            add_reference(item.get("evidence"), "clause", f"条款依据 {index}")
        for index, item in enumerate(structured_fields.get("dates") or [], start=1):
            add_reference(item.get("source_text"), "field", f"日期依据 {index}")
        for index, item in enumerate(structured_fields.get("amounts") or [], start=1):
            add_reference(item.get("source_text"), "field", f"金额依据 {index}")
        for index, item in enumerate(structured_fields.get("owners") or [], start=1):
            add_reference(item.get("source_text"), "field", f"责任人依据 {index}")
        for index, item in enumerate(structured_fields.get("risk_clauses") or [], start=1):
            add_reference(item.get("source_text"), "field", f"风险条款依据 {index}")

        if not references:
            for chunk in chunks[:6]:
                add_reference(chunk.content, "chunk", f"文档片段 {chunk.chunk_index + 1}")

        return references[:8]
