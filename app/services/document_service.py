import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path
from statistics import median

import pdfplumber
from docx import Document as DocxDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Document, DocumentChunk
from app.models.user import User
from app.services.analysis_service import analysis_service
from app.services.document_governance_service import document_governance_service
from app.services.document_job_service import document_job_service
from app.services.document_indexing import build_embedding_id as _build_embedding_id
from app.services.document_qa_service import document_qa_service
from app.services.llm_service import llm_service
from app.services.rag_service import rag_service
from app.services.agentic_rag_service import agentic_rag_service
from app.services.storage_service import storage_service
from app.services.document_parsing import (
    DocumentParsePermanentError,
    _extract_segments,
    _extract_text,
    _facts_describe_same_subject,
    _fallback_summary_from_text,
    _file_to_data_url,
    _normalize_text,
    _prepare_chunks_for_indexing,
    _question_has_visual_hint,
    _safe_int,
    _sha256_bytes,
    _split_text,
    _supports_visual_analysis,
    extract_file_text,
)

UPLOAD_DIR = storage_service.ensure_dir(storage_service.base_dir())
settings = get_settings()

DOCUMENT_STATUS_PARSED = "parsed"
DOCUMENT_STATUS_INDEXED = "indexed"
IMAGE_FILE_TYPES = {"png", ".png", "jpg", ".jpg", "jpeg", ".jpeg", "bmp", ".bmp", "webp", ".webp"}
VISION_SUPPORTED_FILE_TYPES = IMAGE_FILE_TYPES | {"pdf", ".pdf"}




def _try_index_document(document_id: int, chunks: list[dict], *, user_id: int | None = None,
                        knowledge_base_id: int | None = None) -> Exception | None:
    try:
        rag_service.index_document(
            document_id,
            _prepare_chunks_for_indexing(document_id, chunks),
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_status=DOCUMENT_STATUS_INDEXED,
        )
        return None
    except Exception as exc:
        return exc


class DocumentService:
    @staticmethod
    def _parse_metadata_json(raw: str | None) -> dict:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError):
            return {}

    def import_file_document(
        self,
        *,
        db: Session,
        user_id: int,
        title: str,
        file_bytes: bytes,
        file_type: str,
        knowledge_base_name: str | None = None,
        knowledge_base_category: str | None = None,
        classification: str | None = None,
        tags: list[str] | None = None,
        permission_scope: str = "private",
        sensitivity_level: str = "internal",
        permission_users: list[str] | None = None,
        permission_roles: list[str] | None = None,
        metadata: dict | None = None,
    ) -> tuple[Document, bool]:
        file_ext = file_type.lstrip(".") if file_type else "txt"
        unique_name = f"{uuid.uuid4().hex}.{file_ext}"
        file_path = storage_service.save_bytes(base_dir=UPLOAD_DIR, filename=unique_name, content=file_bytes)
        content_hash = _sha256_bytes(file_bytes)
        current_user = db.query(User).filter(User.id == user_id).first()
        knowledge_base = self._resolve_knowledge_base(
            db=db,
            user_id=user_id,
            current_user=current_user,
            knowledge_base_name=knowledge_base_name,
            knowledge_base_category=knowledge_base_category,
            permission_scope=permission_scope,
        )
        doc, created = self._persist_document_record(
            db=db,
            user_id=user_id,
            current_user=current_user,
            title=title,
            file_path=str(file_path),
            file_type=file_ext,
            content_hash=content_hash,
            knowledge_base=knowledge_base,
            classification=classification,
            tags=tags,
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level,
            permission_users=permission_users,
            permission_roles=permission_roles,
            metadata=metadata,
            status=DOCUMENT_STATUS_PARSED,
        )
        if not created:
            return doc, False

        try:
            segments = _extract_segments(str(file_path), file_ext)
            chunks = _split_text(segments)
        except Exception:
            # 解析失败：删除已落库的孤儿行，避免内容哈希去重导致重试永远“已存在”
            db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
            db.delete(doc)
            db.commit()
            raise
        db.add_all(
            [
                DocumentChunk(
                    document_id=doc.id,
                    chunk_index=chunk["chunk_index"],
                    content=chunk["content"],
                    page_number=chunk.get("page_number"),
                    section_title=chunk.get("section_title"),
                    section_path=" > ".join(chunk.get("section_path") or []),
                    segment_type=chunk.get("segment_type"),
                    table_like=bool(chunk.get("table_like")),
                    visual_tags=" ".join(chunk.get("visual_tags") or []),
                    ocr_quality=chunk.get("ocr_quality"),
                    embedding_id=_build_embedding_id(doc.id, chunk["chunk_index"]),
                )
                for chunk in chunks
            ]
        )
        db.commit()
        index_error = _try_index_document(doc.id, chunks, user_id=user_id, knowledge_base_id=doc.knowledge_base_id)
        if index_error is None:
            doc.status = DOCUMENT_STATUS_INDEXED
            db.commit()
        return doc, True

    def _resolve_knowledge_base(
        self,
        *,
        db: Session,
        user_id: int,
        current_user: User | None,
        knowledge_base_name: str | None,
        knowledge_base_category: str | None,
        permission_scope: str,
    ):
        if not knowledge_base_name:
            return None
        return document_governance_service.get_or_create_knowledge_base(
            db=db,
            user_id=user_id,
            name=knowledge_base_name,
            organization_id=current_user.organization_id if current_user else None,
            department_id=current_user.department_id if current_user else None,
            category=knowledge_base_category,
            permission_scope=permission_scope,
        )

    def _persist_document_record(
        self,
        *,
        db: Session,
        user_id: int,
        current_user: User | None,
        title: str,
        file_path: str,
        file_type: str,
        content_hash: str,
        knowledge_base,
        classification: str | None,
        tags: list[str] | None,
        permission_scope: str,
        sensitivity_level: str,
        permission_users: list[str] | None,
        permission_roles: list[str] | None,
        metadata: dict | None,
        status: str,
    ) -> tuple[Document, bool]:
        latest_version = document_governance_service.find_latest_version(
            db=db,
            user_id=user_id,
            title=title,
            content_hash=content_hash,
        )
        if latest_version:
            return latest_version, False

        latest_by_title = document_governance_service.find_latest_version(
            db=db,
            user_id=user_id,
            title=title,
            content_hash=None,
        )
        parent_document_id = (
            latest_by_title.parent_document_id if latest_by_title and latest_by_title.parent_document_id else None
        )
        if latest_by_title and not parent_document_id:
            parent_document_id = latest_by_title.id

        doc = Document(
            user_id=user_id,
            organization_id=current_user.organization_id if current_user else None,
            department_id=current_user.department_id if current_user else None,
            knowledge_base_id=knowledge_base.id if knowledge_base else None,
            parent_document_id=parent_document_id,
            version_number=(latest_by_title.version_number + 1) if latest_by_title else 1,
            title=title,
            file_path=str(file_path),
            file_type=file_type,
            content_hash=content_hash,
            classification=classification,
            tags=json.dumps(tags or [], ensure_ascii=False),
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level or "internal",
            permission_users=json.dumps(permission_users or [], ensure_ascii=False),
            permission_roles=json.dumps(permission_roles or [], ensure_ascii=False),
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            status=status,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        document_governance_service.assign_document_access_rules(
            db=db,
            document_id=doc.id,
            users=permission_users or [],
            roles=permission_roles or [],
        )
        return doc, True

    def import_text_document(
        self,
        *,
        db: Session,
        user_id: int,
        title: str,
        content: str,
        file_type: str = "md",
        knowledge_base_name: str | None = None,
        knowledge_base_category: str | None = None,
        classification: str | None = None,
        tags: list[str] | None = None,
        permission_scope: str = "private",
        sensitivity_level: str = "internal",
        permission_users: list[str] | None = None,
        permission_roles: list[str] | None = None,
        metadata: dict | None = None,
    ) -> tuple[Document, bool]:
        normalized_content = _normalize_text(content or "")
        file_ext = file_type.lstrip(".") if file_type else "md"
        return self.import_file_document(
            db=db,
            user_id=user_id,
            title=title,
            file_bytes=normalized_content.encode("utf-8"),
            file_type=file_ext,
            knowledge_base_name=knowledge_base_name,
            knowledge_base_category=knowledge_base_category,
            classification=classification,
            tags=tags,
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level,
            permission_users=permission_users,
            permission_roles=permission_roles,
            metadata=metadata,
        )

    def upload(
        self,
        file,
        user_id: int,
        db: Session,
        async_mode: bool = False,
        *,
        knowledge_base_name: str | None = None,
        knowledge_base_category: str | None = None,
        classification: str | None = None,
        tags: list[str] | None = None,
        permission_scope: str = "private",
        sensitivity_level: str = "internal",
        permission_users: list[str] | None = None,
        permission_roles: list[str] | None = None,
        metadata: dict | None = None,
    ) -> Document:
        ext = Path(file.filename).suffix
        unique_name = f"{uuid.uuid4().hex}{ext}"
        file_bytes = file.file.read()
        file_path = storage_service.save_bytes(base_dir=UPLOAD_DIR, filename=unique_name, content=file_bytes)

        file_type = ext.lstrip(".") if ext else "txt"
        content_hash = _sha256_bytes(file_bytes)
        current_user = db.query(User).filter(User.id == user_id).first()
        knowledge_base = self._resolve_knowledge_base(
            db=db,
            user_id=user_id,
            current_user=current_user,
            knowledge_base_name=knowledge_base_name,
            knowledge_base_category=knowledge_base_category,
            permission_scope=permission_scope,
        )

        if async_mode:
            doc, created = self._persist_document_record(
                db=db,
                user_id=user_id,
                current_user=current_user,
                title=file.filename,
                file_path=str(file_path),
                file_type=file_type,
                content_hash=content_hash,
                knowledge_base=knowledge_base,
                classification=classification,
                tags=tags,
                permission_scope=permission_scope,
                sensitivity_level=sensitivity_level,
                permission_users=permission_users,
                permission_roles=permission_roles,
                metadata=metadata,
                status="pending",
            )
            if not created:
                return doc

            from app.tasks import parse_document_task

            job = document_job_service.create_job(
                document_id=doc.id,
                user_id=user_id,
                job_type="document_parse",
                db=db,
                current_step="submitted",
                message="文档解析任务已提交",
            )
            task = parse_document_task.delay(doc.id, str(file_path), file_type)
            # 长流程权限快照：保证后台解析期间权限范围稳定。
            snapshot_id = None
            try:
                from app.services.authorization_service import authorization_service

                user_row = db.query(User).filter(User.id == user_id).first()
                if user_row:
                    ctx = authorization_service.build_context(db, user_row)
                    snapshot_id = authorization_service.capture_snapshot(
                        db, user_row, ctx, document_ids=[doc.id],
                    )
            except Exception:
                # 快照失败不阻断上传；文档为创建者所有，访问路径由任务内校验兜底。
                pass
            if snapshot_id:
                task = parse_document_task.delay(doc.id, str(file_path), file_type, snapshot_id)
            document_job_service.attach_task_id(job.id, task.id, db)
            return doc

        segments = _extract_segments(str(file_path), file_type)

        doc, created = self._persist_document_record(
            db=db,
            user_id=user_id,
            current_user=current_user,
            title=file.filename,
            file_path=str(file_path),
            file_type=file_type,
            content_hash=content_hash,
            knowledge_base=knowledge_base,
            classification=classification,
            tags=tags,
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level,
            permission_users=permission_users,
            permission_roles=permission_roles,
            metadata=metadata,
            status=DOCUMENT_STATUS_PARSED,
        )
        if not created:
            return doc

        chunks = _split_text(segments)
        db_chunks = [
            DocumentChunk(
                document_id=doc.id,
                chunk_index=chunk["chunk_index"],
                content=chunk["content"],
                page_number=chunk.get("page_number"),
                section_title=chunk.get("section_title"),
                section_path=" > ".join(chunk.get("section_path") or []),
                segment_type=chunk.get("segment_type"),
                table_like=bool(chunk.get("table_like")),
                visual_tags=" ".join(chunk.get("visual_tags") or []),
                ocr_quality=chunk.get("ocr_quality"),
                embedding_id=_build_embedding_id(doc.id, chunk["chunk_index"]),
            )
            for chunk in chunks
        ]
        db.add_all(db_chunks)
        db.commit()

        index_error = _try_index_document(doc.id, chunks, user_id=user_id, knowledge_base_id=doc.knowledge_base_id)
        if index_error is None:
            doc.status = DOCUMENT_STATUS_INDEXED
            db.commit()
        return doc

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
        return _extract_text(doc.file_path, doc.file_type)

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
                visual_analysis = asyncio.run(
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

        image_url = _file_to_data_url(doc.file_path, doc.file_type)
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

    @staticmethod
    def _conflict_locator(source_text: str | None, chunks: list[DocumentChunk]) -> dict:
        source = (source_text or "").strip()
        matched_chunk = None
        if source:
            normalized_source = re.sub(r"\s+", "", source)
            for chunk in chunks:
                normalized_chunk = re.sub(r"\s+", "", chunk.content or "")
                if normalized_source in normalized_chunk or normalized_chunk in normalized_source:
                    matched_chunk = chunk
                    break
        return {
            "source_text": source or None,
            "chunk_id": matched_chunk.id if matched_chunk else None,
            "page_number": matched_chunk.page_number if matched_chunk else None,
            "section_title": matched_chunk.section_title if matched_chunk else None,
            "section_path": matched_chunk.section_path if matched_chunk else None,
        }

    def _build_conflict_fact(
        self,
        *,
        document: dict,
        field_type: str,
        item: dict,
        chunks: list[DocumentChunk],
    ) -> dict | None:
        if field_type == "dates":
            value = str(item.get("normalized_date") or item.get("value") or "").strip()
            subject = str(item.get("description") or "").strip()
        elif field_type == "amounts":
            value = str(item.get("amount") or item.get("value") or "").strip()
            subject = str(item.get("description") or "").strip()
        elif field_type == "owners":
            value = str(item.get("name") or "").strip()
            subject = str(item.get("responsibility") or item.get("role") or "").strip()
        else:
            return None
        if not value or not subject:
            return None
        locator = self._conflict_locator(item.get("source_text"), chunks)
        return {
            "document_id": document["document_id"],
            "document_title": document["title"],
            "field_type": field_type,
            "field": subject,
            "value": value,
            **locator,
        }

    @staticmethod
    def _facts_have_same_value(left: dict, right: dict) -> bool:
        def normalize_value(value: str) -> str:
            return re.sub(r"[\s,，。；;：:\-_/]+", "", (value or "").lower())

        return normalize_value(left["value"]) == normalize_value(right["value"])

    def _detect_cross_document_conflicts(self, analyses: list[dict], db: Session) -> dict:
        facts: list[dict] = []
        for document in analyses:
            chunks = (
                db.query(DocumentChunk)
                .filter(DocumentChunk.document_id == document["document_id"])
                .order_by(DocumentChunk.chunk_index.asc())
                .all()
            )
            fields = document.get("structured_fields") or {}
            for field_type in ("dates", "amounts", "owners"):
                for item in fields.get(field_type) or []:
                    if isinstance(item, dict):
                        fact = self._build_conflict_fact(
                            document=document,
                            field_type=field_type,
                            item=item,
                            chunks=chunks,
                        )
                        if fact:
                            facts.append(fact)

        severity_by_type = {"dates": "high", "amounts": "high", "owners": "medium"}
        action_by_type = {
            "dates": "确认最终时间基线，并同步更新计划和会议结论。",
            "amounts": "核对审批版本、合同条款与预算口径后确认最终金额。",
            "owners": "确认唯一责任人，并在任务中明确交付边界和截止时间。",
        }
        label_by_type = {"dates": "日期", "amounts": "金额", "owners": "负责人"}
        conflicts: list[dict] = []
        seen: set[tuple] = set()
        compared_pairs = 0
        for index, left in enumerate(facts):
            for right in facts[index + 1 :]:
                if left["document_id"] == right["document_id"] or left["field_type"] != right["field_type"]:
                    continue
                if not _facts_describe_same_subject(left["field"], right["field"]):
                    continue
                compared_pairs += 1
                if self._facts_have_same_value(left, right):
                    continue
                key = (
                    left["field_type"],
                    tuple(sorted((left["document_id"], right["document_id"]))),
                    tuple(sorted((left["value"], right["value"]))),
                )
                if key in seen:
                    continue
                seen.add(key)
                evidence_complete = all(
                    source.get("source_text")
                    and (source.get("chunk_id") is not None or source.get("page_number") is not None or source.get("section_title"))
                    for source in (left, right)
                )
                conflicts.append(
                    {
                        "field_type": left["field_type"],
                        "field_label": label_by_type[left["field_type"]],
                        "field": left["field"],
                        "source_a": left,
                        "source_b": right,
                        "severity": severity_by_type[left["field_type"]],
                        "recommended_action": action_by_type[left["field_type"]],
                        "evidence_complete": evidence_complete,
                        "status": "confirmed" if evidence_complete else "needs_evidence",
                    }
                )
        conflicts.sort(key=lambda item: (item["evidence_complete"] is False, item["severity"] != "high", item["field"]))
        return {
            "facts_extracted": len(facts),
            "comparable_pairs": compared_pairs,
            "conflicts": conflicts,
            "confirmed_conflict_count": sum(1 for item in conflicts if item["evidence_complete"]),
            "needs_evidence_count": sum(1 for item in conflicts if not item["evidence_complete"]),
        }

    async def compare(
        self,
        document_ids: list[int],
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        max_length: int = 500,
        ) -> dict:
        if len(document_ids) < 2:
            raise ValueError("At least two documents are required for comparison")
        if len(document_ids) > 5:
            raise ValueError("At most five documents can be compared at one time")

        analyses = []
        for document_id in document_ids:
            doc = self.get(document_id, db, user_id=user_id, role=role, organization_id=organization_id, department_id=department_id)
            if not doc:
                raise ValueError(f"Document not found: {document_id}")

            analysis = await self.analyze(
                document_id,
                db,
                user_id=user_id,
                role=role,
                organization_id=organization_id,
                department_id=department_id,
                max_length=max_length,
            )
            analyses.append(
                {
                    "document_id": document_id,
                    "title": doc.title,
                    "summary": analysis["summary"],
                    "risks": analysis["risks"],
                    "todos": analysis["todos"],
                    "structured_fields": analysis.get(
                        "structured_fields",
                        {"dates": [], "amounts": [], "owners": [], "risk_clauses": []},
                    ),
                    "references": analysis["references"],
                    "risks_text": "；".join(
                        [f"{item.get('title', '')}:{item.get('description', '')}" for item in analysis["risks"][:5]]
                    ),
                    "todos_text": "；".join(
                        [f"{item.get('title', '')}:{item.get('description', '')}" for item in analysis["todos"][:5]]
                    ),
                }
            )

        comparison = await analysis_service.compare_documents(analyses, user_id=user_id)
        conflict_analysis = self._detect_cross_document_conflicts(analyses, db)
        comparison["conflict_analysis"] = conflict_analysis
        summary_cards = [
            {
                "document_id": item["document_id"],
                "title": item["title"],
                "summary": item["summary"],
                "risk_count": len(item["risks"]),
                "todo_count": len(item["todos"]),
                "reference_count": len(item["references"]),
            }
            for item in analyses
        ]
        return {
            "document_ids": document_ids,
            "documents": analyses,
            "summary_cards": summary_cards,
            "comparison": comparison,
        }

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
        return _extract_text(doc.file_path, doc.file_type)

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
    ) -> list[Document]:
        from app.services.authorization_service import authorization_service

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
        rows = q.order_by(Document.created_at.desc(), Document.id.desc()).all()

        filtered = []
        for doc in rows:
            if connector_id is not None:
                metadata = self._parse_metadata_json(doc.metadata_json)
                if _safe_int(metadata.get("connector_id"), 0) != connector_id:
                    continue
            if query and query not in (doc.title or ""):
                continue
            filtered.append(doc)
        return filtered

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


document_service = DocumentService()
