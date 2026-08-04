from __future__ import annotations

import json
from typing import Any

from app.core.database import SessionLocal
from app.core.observability_sanitizer import (
    sanitize_observability_error_message,
    sanitize_observability_excerpt,
    to_observability_excerpt,
)
from app.models.llm_call_log import LLMCallLog


class LLMObservabilityService:
    def log_event(
        self,
        *,
        module_name: str,
        action: str,
        model_name: str,
        status: str = "success",
        duration_ms: int | None = None,
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        error_message: str | None = None,
        request_excerpt: Any = None,
        response_excerpt: Any = None,
    ) -> None:
        db = SessionLocal()
        try:
            db.add(
                LLMCallLog(
                    user_id=user_id,
                    module_name=module_name,
                    action=action,
                    model_name=model_name,
                    prompt_template=prompt_template,
                    prompt_version=prompt_version,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    duration_ms=duration_ms,
                    status=status,
                    error_message=sanitize_observability_error_message(action, to_observability_excerpt(error_message)),
                    request_excerpt=sanitize_observability_excerpt(
                        action,
                        to_observability_excerpt(request_excerpt),
                        kind="request",
                    ),
                    response_excerpt=sanitize_observability_excerpt(
                        action,
                        to_observability_excerpt(response_excerpt),
                        kind="response",
                    ),
                )
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


llm_observability_service = LLMObservabilityService()
