import asyncio

from sqlalchemy.orm import Session

from app.core.async_utils import run_async
from app.models.document import Document
from app.services.documents.analysis_service import analysis_service
from app.services.documents.document_governance_service import document_governance_service
from app.services.documents.document_parsing import (
    _extract_text,
    _fallback_summary_from_text,
    _file_to_data_url,
    _question_has_visual_hint,
    _supports_visual_analysis,
)
from app.services.documents.document_pipeline import resolve_local_path
from app.services.documents.document_qa_service import document_qa_service
from app.services.llm.llm_service import llm_service
from app.services.rag.agentic_rag_service import agentic_rag_service

IMAGE_FILE_TYPES = {"png", ".png", "jpg", ".jpg", "jpeg", ".jpeg", "bmp", ".bmp", "webp", ".webp"}
VISION_SUPPORTED_FILE_TYPES = IMAGE_FILE_TYPES | {"pdf", ".pdf"}


class ReadAnalyzeMixin:
    def get(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> Document | None:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return None
        if user_id is None:
            return doc
        return doc if document_governance_service.can_access_document(
            db=db,
            document=doc,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        ) else None

    def summarize(
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

    def ask(
        self,
        document_id: int,
        question: str,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> dict:
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
        final_question = question
        visual_analysis = None
        if _supports_visual_analysis(doc.file_type) and not _question_has_visual_hint(question):
            try:
                visual_analysis = run_async(
                    self.analyze_visual(
                        document_id=document_id,
                        prompt=f"请仅提取与这个问题最相关的视觉线索：{question}",
                        db=db,
                        user_id=doc.user_id,
                    )
                )
            except Exception:
                visual_analysis = None
        if visual_analysis and visual_analysis.get("analysis"):
            final_question = f"{question}\n\n补充视觉分析线索：{visual_analysis['analysis']}"

        result = agentic_rag_service.answer(
            final_question,
            document_id=document_id,
            user_id=doc.user_id,
            authorized_document_ids=[document_id],
        )
        qa_record = document_qa_service.record(
            document_id=document_id,
            user_id=doc.user_id,
            question=question,
            answer=result["answer"],
            db=db,
            citations=result["citations"],
            hit_chunks=result["hit_chunks"],
            latency_ms=result["latency_ms"],
            source="document",
        )
        return {
            "qa_record_id": qa_record.id,
            "answer": result["answer"],
            "citations": result["citations"],
            "confidence": result["confidence"],
            "can_answer": result["can_answer"],
            "agentic_rag": result.get("agentic_rag"),
            "feedback_value": qa_record.feedback_value,
            "feedback_status": qa_record.feedback_status,
        }

    async def analyze_visual(
        self,
        document_id: int,
        prompt: str,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> dict:
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
        if doc.file_type.lower() not in VISION_SUPPORTED_FILE_TYPES:
            raise ValueError("Document visual analysis only supports image and PDF files")

        image_url = _file_to_data_url(str(resolve_local_path(doc)), doc.file_type)
        analysis = await llm_service.generate_with_images(
            prompt,
            image_urls=[image_url],
            temperature=0.2,
            action="document_visual_analyze",
            user_id=doc.user_id,
        )
        return {
            "document_id": doc.id,
            "title": doc.title,
            "file_type": doc.file_type,
            "analysis": analysis.strip(),
            "image_count": 1,
        }

    async def analyze(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        max_length: int = 500,
    ) -> dict:
        raw_text = self._get_document_text(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        summary_result, risks_result, todos_result, clauses_result, fields_result = await asyncio.gather(
            analysis_service.summarize_document(raw_text, max_length=max_length, user_id=user_id),
            analysis_service.extract_document_risks(raw_text, user_id=user_id),
            analysis_service.extract_document_todos(raw_text, user_id=user_id),
            analysis_service.extract_document_clauses(raw_text, user_id=user_id),
            analysis_service.extract_document_fields(raw_text, user_id=user_id),
            return_exceptions=True,
        )

        warnings: list[dict] = []
        summary = summary_result
        if isinstance(summary_result, Exception):
            summary = _fallback_summary_from_text(raw_text, max_length=max_length)
            warnings.append(
                {
                    "stage": "summary",
                    "message": str(summary_result),
                    "fallback_applied": True,
                }
            )
        risks = risks_result if not isinstance(risks_result, Exception) else []
        if isinstance(risks_result, Exception):
            warnings.append(
                {
                    "stage": "risks",
                    "message": str(risks_result),
                    "fallback_applied": True,
                }
            )
        todos = todos_result if not isinstance(todos_result, Exception) else []
        if isinstance(todos_result, Exception):
            warnings.append(
                {
                    "stage": "todos",
                    "message": str(todos_result),
                    "fallback_applied": True,
                }
            )
        clauses = clauses_result if not isinstance(clauses_result, Exception) else []
        if isinstance(clauses_result, Exception):
            warnings.append(
                {
                    "stage": "clauses",
                    "message": str(clauses_result),
                    "fallback_applied": True,
                }
            )
        structured_fields = fields_result if not isinstance(fields_result, Exception) else {
            "dates": [],
            "amounts": [],
            "owners": [],
            "risk_clauses": [],
        }
        if isinstance(fields_result, Exception):
            warnings.append(
                {
                    "stage": "structured_fields",
                    "message": str(fields_result),
                    "fallback_applied": True,
                }
            )

        doc = self.get(document_id, db, user_id=user_id, role=role, organization_id=organization_id, department_id=department_id)
        if doc:
            doc.summary = summary
            db.commit()

        chunks = self.get_chunks(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
            limit=8,
        )
        references = self._build_references(
            risks=risks,
            todos=todos,
            clauses=clauses,
            structured_fields=structured_fields,
            chunks=chunks,
        )

        return {
            "document_id": document_id,
            "summary": summary,
            "risks": risks,
            "todos": todos,
            "clauses": clauses,
            "structured_fields": structured_fields,
            "references": references,
            "analysis_status": "partial" if warnings else "success",
            "analysis_warnings": warnings,
        }
