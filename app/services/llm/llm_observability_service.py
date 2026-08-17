from __future__ import annotations

from typing import Any

from app.core.database import SessionLocal
from app.core.observability import classify_error_category
from app.core.obs_context import get_context
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
        # P0 出站数据保护审计字段（可选；缺失保持旧行为）。
        provider: str | None = None,
        data_level: str | None = None,
        pii_hit_codes: str | None = None,
        pii_hit_count: int = 0,
        redacted_count: int = 0,
        blocked_reason: str | None = None,
    ) -> None:
        # P1 链路关联：统一上下文（API/Celery headers 传播）补齐 trace/task/agent_run/org。
        ctx = get_context()
        trace_id = ctx.trace_id
        task_id = ctx.task_id
        agent_run_id = ctx.agent_run_id
        organization_id = ctx.org_id
        # 稳定错误类别（聚合标签用，禁止异常文本作 label）。
        error_category = classify_error_category(error_message) if error_message else None

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
                    trace_id=trace_id,
                    task_id=task_id,
                    agent_run_id=agent_run_id,
                    organization_id=organization_id,
                    error_category=error_category,
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
                    provider=provider,
                    data_level=data_level,
                    pii_hit_codes=pii_hit_codes,
                    pii_hit_count=pii_hit_count,
                    redacted_count=redacted_count,
                    blocked_reason=blocked_reason,
                )
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

        # P1 指标：LLM 成功率/延迟（进程内非阻塞；model/status/error_category 均为有限枚举）。
        try:
            from app.core.metrics import metrics

            metrics.increment(
                "llm_calls",
                labels={"model": model_name, "status": status, "error_category": error_category or "none"},
            )
            if duration_ms is not None:
                metrics.observe("llm_latency_ms", duration_ms, labels={"model": model_name})
        except Exception:  # noqa: BLE001 - 指标采集失败不影响业务
            pass
        # P1 model 类型结构化日志（OBS_MODEL_DETAIL_LOG_ENABLED 开启时；只记摘要）。
        try:
            from app.core.observability import log_model_event

            log_model_event(
                event_name=action,
                module=module_name,
                model_name=model_name,
                status=status,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                error_code=error_category,
                error_category=error_category,
            )
        except Exception:  # noqa: BLE001
            pass


llm_observability_service = LLMObservabilityService()
