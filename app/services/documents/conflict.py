import re

from sqlalchemy.orm import Session

from app.models.document import DocumentChunk
from app.services.documents.analysis_service import analysis_service
from app.services.documents.document_parsing import _facts_describe_same_subject


class ConflictMixin:
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
