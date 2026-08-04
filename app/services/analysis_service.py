from app.services.llm_service import llm_service
from app.services.prompt_service import prompt_service


def _trim_text(text: str, max_chars: int) -> str:
    return text[:max_chars] if len(text) > max_chars else text


def _normalize_string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                result.append(normalized)
    return result


def _normalize_difference_list(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                result.append({"title": f"差异 {index}", "detail": normalized})
            continue
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"差异 {index}").strip()
        detail = str(item.get("detail") or item.get("description") or "").strip()
        if detail:
            result.append({"title": title, "detail": detail})
    return result


def _normalize_risk_delta_list(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                result.append({"title": f"风险差异 {index}", "detail": normalized})
            continue
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"风险差异 {index}").strip()
        detail = str(item.get("detail") or item.get("description") or "").strip()
        severity = str(item.get("severity") or "").strip().lower() or None
        if detail:
            result.append({"title": title, "detail": detail, "severity": severity})
    return result


def _normalize_document_field_items(value, field_type: str) -> list[dict]:
    if not isinstance(value, list):
        return []

    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if field_type == "dates":
            raw_value = str(item.get("value") or "").strip()
            description = str(item.get("description") or "").strip()
            source_text = str(item.get("source_text") or item.get("evidence") or "").strip()
            normalized_date = str(item.get("normalized_date") or "").strip() or None
            if raw_value:
                result.append(
                    {
                        "value": raw_value,
                        "normalized_date": normalized_date,
                        "description": description or None,
                        "source_text": source_text or None,
                    }
                )
        elif field_type == "amounts":
            raw_value = str(item.get("value") or "").strip()
            amount = str(item.get("amount") or raw_value).strip()
            currency = str(item.get("currency") or "").strip() or None
            description = str(item.get("description") or "").strip()
            source_text = str(item.get("source_text") or item.get("evidence") or "").strip()
            if raw_value or amount:
                result.append(
                    {
                        "value": raw_value or amount,
                        "amount": amount or raw_value,
                        "currency": currency,
                        "description": description or None,
                        "source_text": source_text or None,
                    }
                )
        elif field_type == "owners":
            name = str(item.get("name") or "").strip()
            responsibility = str(item.get("responsibility") or item.get("description") or "").strip()
            role = str(item.get("role") or "").strip() or None
            source_text = str(item.get("source_text") or item.get("evidence") or "").strip()
            if name or responsibility:
                result.append(
                    {
                        "name": name or None,
                        "role": role,
                        "responsibility": responsibility or None,
                        "source_text": source_text or None,
                    }
                )
        elif field_type == "risk_clauses":
            title = str(item.get("title") or "").strip()
            description = str(item.get("description") or item.get("content") or "").strip()
            severity = str(item.get("severity") or "").strip().lower() or None
            source_text = str(item.get("source_text") or item.get("evidence") or "").strip()
            suggestion = str(item.get("suggestion") or "").strip() or None
            if title or description:
                result.append(
                    {
                        "title": title or None,
                        "description": description or None,
                        "severity": severity,
                        "source_text": source_text or None,
                        "suggestion": suggestion,
                    }
                )
    return result


def _normalize_document_fields(payload: dict) -> dict:
    if not isinstance(payload, dict):
        payload = {}
    return {
        "dates": _normalize_document_field_items(payload.get("dates"), "dates"),
        "amounts": _normalize_document_field_items(payload.get("amounts"), "amounts"),
        "owners": _normalize_document_field_items(payload.get("owners"), "owners"),
        "risk_clauses": _normalize_document_field_items(payload.get("risk_clauses"), "risk_clauses"),
    }


def _normalize_doc_compare_result(payload: dict, documents: list[dict]) -> dict:
    overview = str(payload.get("overview") or "").strip()
    common_points = _normalize_string_list(payload.get("common_points"))
    differences = _normalize_difference_list(payload.get("differences"))
    risk_delta = _normalize_risk_delta_list(payload.get("risk_delta"))
    action_suggestions = _normalize_string_list(payload.get("action_suggestions"))

    if not overview:
        titles = [item.get("title") or f"文档 {index + 1}" for index, item in enumerate(documents)]
        overview = f"本次共对比 {len(documents)} 份文档：{'、'.join(titles)}。请结合差异项与风险差异判断版本变化和潜在影响。"

    return {
        "overview": overview,
        "comparison_type": "document_diff",
        "document_count": len(documents),
        "common_points": common_points,
        "differences": differences,
        "risk_delta": risk_delta,
        "action_suggestions": action_suggestions,
    }


class AnalysisService:
    async def summarize_document(self, text: str, max_length: int = 500, user_id: int | None = None) -> str:
        metadata = prompt_service.get_template_metadata("document_summary", user_id=user_id)
        prompt = prompt_service.render_by_name(
            "document_summary",
            user_id=user_id,
            document_content=_trim_text(text, 8000),
            max_length=max_length,
        )
        return await llm_service.generate(
            prompt,
            temperature=0.3,
            action="document_summary",
            user_id=user_id,
            prompt_template=metadata.get("prompt_template"),
            prompt_version=metadata.get("prompt_version"),
        )

    async def extract_document_risks(self, text: str, user_id: int | None = None) -> list[dict]:
        prompt = prompt_service.render_by_name(
            "document_risk_extract",
            user_id=user_id,
            document_content=_trim_text(text, 8000),
        )
        return await self._extract_json_array(prompt, action="document_risk_extract", user_id=user_id)

    async def extract_document_todos(self, text: str, user_id: int | None = None) -> list[dict]:
        prompt = prompt_service.render_by_name(
            "document_todo_extract",
            user_id=user_id,
            document_content=_trim_text(text, 8000),
        )
        return await self._extract_json_array(prompt, action="document_todo_extract", user_id=user_id)

    async def extract_document_clauses(self, text: str, user_id: int | None = None) -> list[dict]:
        prompt = prompt_service.render_by_name(
            "document_clause_extract",
            user_id=user_id,
            document_content=_trim_text(text, 8000),
        )
        return await self._extract_json_array(prompt, action="document_clause_extract", user_id=user_id)

    async def extract_document_fields(self, text: str, user_id: int | None = None) -> dict:
        prompt = prompt_service.render_by_name(
            "document_field_extract",
            user_id=user_id,
            document_content=_trim_text(text, 8000),
        )
        result = await self._extract_json_object(prompt, action="document_field_extract", user_id=user_id)
        return _normalize_document_fields(result)

    async def compare_documents(self, documents: list[dict], user_id: int | None = None) -> dict:
        doc_blocks = []
        for index, item in enumerate(documents, start=1):
            doc_blocks.append(
                "\n".join(
                    [
                        f"文档{index} 标题：{item.get('title', '')}",
                        f"文档{index} 摘要：{item.get('summary', '')}",
                        f"文档{index} 风险：{item.get('risks_text', '') or '无'}",
                        f"文档{index} 待办：{item.get('todos_text', '') or '无'}",
                    ]
                )
            )
        prompt = prompt_service.render_by_name(
            "document_compare",
            user_id=user_id,
            document_blocks=_trim_text("\n\n".join(doc_blocks), 12000),
        )
        result = await self._extract_json_object(prompt, action="document_compare", user_id=user_id)
        return _normalize_doc_compare_result(result, documents)

    async def summarize_meeting(self, transcript: str, user_id: int | None = None) -> dict:
        prompt = prompt_service.render_by_name(
            "meeting_summary",
            user_id=user_id,
            meeting_content=_trim_text(transcript, 12000),
        )
        return await self._extract_json_object(prompt, action="meeting_summary", user_id=user_id)

    async def extract_meeting_decisions(self, transcript: str, user_id: int | None = None) -> list[dict]:
        prompt = prompt_service.render_by_name(
            "meeting_decision_extract",
            user_id=user_id,
            meeting_content=_trim_text(transcript, 12000),
        )
        return await self._extract_json_array(prompt, action="meeting_decision_extract", user_id=user_id)

    async def extract_meeting_topics(self, transcript: str, user_id: int | None = None) -> list[dict]:
        prompt = prompt_service.render_by_name(
            "meeting_topic_extract",
            user_id=user_id,
            meeting_content=_trim_text(transcript, 12000),
        )
        return await self._extract_json_array(prompt, action="meeting_topic_extract", user_id=user_id)

    async def extract_tasks_from_chat(self, message: str, user_id: int | None = None) -> list[dict]:
        prompt = prompt_service.render_by_name(
            "task_extract_from_chat",
            user_id=user_id,
            message=message,
        )
        return await self._extract_json_array(prompt, action="task_extract_from_chat", user_id=user_id)

    async def decompose_task(self, title: str, description: str | None, user_id: int | None = None) -> list[dict]:
        prompt = prompt_service.render_by_name(
            "task_decompose",
            user_id=user_id,
            title=title,
            description=description or "",
        )
        return await self._extract_json_array(prompt, action="task_decompose", user_id=user_id)

    async def _extract_json_array(self, prompt: str, action: str, user_id: int | None = None) -> list[dict]:
        metadata = prompt_service.get_template_metadata(action, user_id=user_id)
        raw = await llm_service.generate(
            prompt,
            temperature=0.3,
            action=action,
            user_id=user_id,
            prompt_template=metadata.get("prompt_template"),
            prompt_version=metadata.get("prompt_version"),
        )
        return llm_service.parse_json_array(raw)

    async def _extract_json_object(self, prompt: str, action: str, user_id: int | None = None) -> dict:
        metadata = prompt_service.get_template_metadata(action, user_id=user_id)
        raw = await llm_service.generate(
            prompt,
            temperature=0.3,
            action=action,
            user_id=user_id,
            prompt_template=metadata.get("prompt_template"),
            prompt_version=metadata.get("prompt_version"),
        )
        return llm_service.parse_json_object(raw)


analysis_service = AnalysisService()
