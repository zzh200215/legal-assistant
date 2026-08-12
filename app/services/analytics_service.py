import re
import json
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.agent import AgentRun, ToolCallLog
from app.models.document import Document, DocumentQARecord
from app.models.email import EmailSendRequest
from app.models.llm_call_log import LLMCallLog
from app.models.operation_log import OperationLog
from app.models.prompt import PromptTemplate
from app.models.token_usage import TokenUsage
from app.services.document_qa_service import document_qa_service
from app.services.llm_governance_service import llm_governance_service
from app.services.analytics_task_state import (
    extract_max_length,
    extract_task_id,
    normalize_async_state,
)
from eval.bundle_utils import DEFAULT_BASELINE_SNAPSHOT_PATH, DEFAULT_OUTPUT_DIR
from app.tasks import (
    analyze_document_task,
    parse_document_task,
    summarize_document_task,
)
from app.core.celery_app import celery_app

settings = get_settings()

ALERT_DATE_FORMAT = "%Y-%m-%d"


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _classify_alert(
    *,
    source: str,
    title: str | None,
    message: str | None,
    target_type: str | None = None,
) -> dict:
    text = " ".join(part for part in [title, message, target_type] if part).lower()

    category = "system_error"
    error_type = "unknown_error"
    severity = "medium"

    if source == "outbound_email":
        if "approval_pending" in text:
            category = "approval_pending"
            error_type = "outbound_approval_pending"
            severity = "medium"
        else:
            category = "outbound_email_error"
            error_type = "smtp_delivery_failed"
            severity = "high"
    elif _contains_any(text, ("timeout", "timed out", "超时")):
        category = "timeout_error"
        error_type = "timeout"
        severity = "high"
    elif _contains_any(text, ("permission", "forbidden", "unauthorized", "403", "无权", "权限", "未授权")):
        category = "permission_error"
        error_type = "permission_denied"
        severity = "medium"
    elif _contains_any(text, ("network", "connection", "dns", "socket", "connect", "网络", "连接")):
        category = "network_error"
        error_type = "network_failure"
        severity = "high"
    elif _contains_any(text, ("openai", "model", "llm", "token", "context_length", "rate limit", "模型")):
        category = "model_error"
        error_type = "model_failure"
        severity = "high"
    elif _contains_any(text, ("tool", "工具", "observation", "action", "参数校验", "invalid params", "parameter")):
        category = "tool_error"
        error_type = "tool_execution_failed"
        severity = "high"
    elif _contains_any(
        text,
        (
            "not found",
            "不存在",
            "missing",
            "validation",
            "json",
            "parse",
            "schema",
            "empty",
            "null",
            "数据",
        ),
    ):
        category = "data_error"
        error_type = "data_validation_failed"
        severity = "medium"
    elif source == "agent":
        category = "agent_error"
        error_type = "agent_execution_failed"
        severity = "high"
    elif source == "async_task":
        category = "async_task_error"
        error_type = "async_task_failed"
        severity = "high"

    return {
        "category": category,
        "error_type": error_type,
        "severity": severity,
        "source_label": {
            "agent": "Agent",
            "async_task": "异步任务",
            "outbound_email": "外发邮件",
        }.get(source, source),
    }


def _build_alert_stats(alerts: list[dict]) -> dict:
    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_date: dict[str, int] = {}
    by_error_type: dict[str, int] = {}

    for alert in alerts:
        source = alert.get("source") or "unknown"
        category = alert.get("category") or "unknown"
        severity = alert.get("severity") or "unknown"
        error_type = alert.get("error_type") or "unknown"
        created_at = alert.get("created_at")
        date_key = created_at.strftime(ALERT_DATE_FORMAT) if created_at else "unknown"

        by_source[source] = by_source.get(source, 0) + 1
        by_category[category] = by_category.get(category, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_error_type[error_type] = by_error_type.get(error_type, 0) + 1
        by_date[date_key] = by_date.get(date_key, 0) + 1

    return {
        "total": len(alerts),
        "by_source": by_source,
        "by_category": by_category,
        "by_severity": by_severity,
        "by_error_type": by_error_type,
        "by_date": by_date,
    }


def _task_title_from_action(action: str | None, target_type: str | None) -> str:
    mapping = {
        "document_parse": "文档解析",
        "document_summary": "文档摘要",
        "document_analysis": "文档分析",
    }
    action = action or ""
    for prefix, label in mapping.items():
        if action.startswith(prefix):
            return label
    return f"{target_type or '任务'}执行"


class AnalyticsService:
    @staticmethod
    def _safe_json_loads(payload: str | None) -> dict:
        if not payload:
            return {}
        try:
            data = json.loads(payload)
            return data if isinstance(data, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}

    def list_llm_calls(
        self,
        db: Session,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        limit: int = 100,
        module_name: str | None = None,
        action: str | None = None,
        status: str | None = None,
    ) -> list[LLMCallLog]:
        since = utc_now() - timedelta(days=days)
        query = db.query(LLMCallLog).filter(LLMCallLog.created_at >= since)
        if user_id is not None and not include_all_users:
            query = query.filter(LLMCallLog.user_id == user_id)
        if module_name:
            query = query.filter(LLMCallLog.module_name == module_name)
        if action:
            query = query.filter(LLMCallLog.action == action)
        if status:
            query = query.filter(LLMCallLog.status == status)
        return query.order_by(LLMCallLog.created_at.desc()).limit(limit).all()

    def get_llm_call_stats(
        self,
        db: Session,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        module_name: str | None = None,
        action: str | None = None,
        status: str | None = None,
    ) -> dict:
        rows = self.list_llm_calls(
            db=db,
            user_id=user_id,
            include_all_users=include_all_users,
            days=days,
            limit=2000,
            module_name=module_name,
            action=action,
            status=status,
        )

        total_calls = len(rows)
        failed_calls = sum(1 for row in rows if row.status != "success")
        total_input_tokens = sum(row.input_tokens or 0 for row in rows)
        total_output_tokens = sum(row.output_tokens or 0 for row in rows)
        total_duration = sum(row.duration_ms or 0 for row in rows)

        by_module = {}
        by_action = {}
        by_status = {}
        by_date = {}
        failed_by_date = {}
        rag_pipeline_count = 0
        rag_refusal_count = 0
        rag_answered_count = 0
        agent_run_count = 0
        stage_totals = {
            "rag_retrieval_duration_ms": 0,
            "rag_rerank_duration_ms": 0,
            "rag_generation_duration_ms": 0,
            "agent_run_duration_ms": 0,
        }

        for row in rows:
            module_key = row.module_name or "unknown"
            action_key = row.action or "unknown"
            status_key = row.status or "unknown"
            date_key = row.created_at.strftime("%Y-%m-%d") if row.created_at else "unknown"

            by_module[module_key] = by_module.get(module_key, 0) + 1
            by_status[status_key] = by_status.get(status_key, 0) + 1
            by_date[date_key] = by_date.get(date_key, 0) + 1
            if row.status != "success":
                failed_by_date[date_key] = failed_by_date.get(date_key, 0) + 1

            if action_key not in by_action:
                by_action[action_key] = {"calls": 0, "failed": 0}
            by_action[action_key]["calls"] += 1
            if row.status != "success":
                by_action[action_key]["failed"] += 1

            response_payload = self._safe_json_loads(row.response_excerpt)
            if action_key == "rag_pipeline":
                rag_pipeline_count += 1
                if row.status == "refused" or response_payload.get("result_status") == "refused":
                    rag_refusal_count += 1
                if row.status == "success" or response_payload.get("result_status") == "answered":
                    rag_answered_count += 1
                stage_totals["rag_retrieval_duration_ms"] += int(response_payload.get("retrieval_duration_ms") or 0)
                stage_totals["rag_rerank_duration_ms"] += int(response_payload.get("rerank_duration_ms") or 0)
                stage_totals["rag_generation_duration_ms"] += int(response_payload.get("generation_duration_ms") or 0)
            if action_key == "agent_run":
                agent_run_count += 1
                stage_totals["agent_run_duration_ms"] += int(row.duration_ms or 0)

        return {
            "days": days,
            "total_calls": total_calls,
            "failed_calls": failed_calls,
            "success_rate": round(((total_calls - failed_calls) / total_calls), 4) if total_calls else 0,
            "avg_duration_ms": round(total_duration / total_calls) if total_calls else 0,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "by_module": by_module,
            "by_action": by_action,
            "by_status": by_status,
            "by_date": by_date,
            "failed_by_date": failed_by_date,
            "pipeline_stats": {
                "rag_pipeline_runs": rag_pipeline_count,
                "rag_answered_runs": rag_answered_count,
                "rag_refusal_runs": rag_refusal_count,
                "rag_refusal_rate": round(rag_refusal_count / rag_pipeline_count, 4) if rag_pipeline_count else 0,
                "agent_run_count": agent_run_count,
            },
            "stage_avg_duration_ms": {
                "rag_retrieval_duration_ms": round(stage_totals["rag_retrieval_duration_ms"] / rag_pipeline_count)
                if rag_pipeline_count
                else 0,
                "rag_rerank_duration_ms": round(stage_totals["rag_rerank_duration_ms"] / rag_pipeline_count)
                if rag_pipeline_count
                else 0,
                "rag_generation_duration_ms": round(stage_totals["rag_generation_duration_ms"] / rag_pipeline_count)
                if rag_pipeline_count
                else 0,
                "agent_run_duration_ms": round(stage_totals["agent_run_duration_ms"] / agent_run_count)
                if agent_run_count
                else 0,
            },
        }

    @staticmethod
    def _parse_model_pricing() -> tuple[str, dict[str, dict[str, float]]]:
        currency = (settings.LLM_PRICE_CURRENCY or "CNY").strip().upper() or "CNY"
        raw = (settings.LLM_MODEL_PRICING or "").strip()
        if not raw:
            return currency, {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return currency, {}
        pricing: dict[str, dict[str, float]] = {}
        if not isinstance(payload, dict):
            return currency, pricing
        for model_name, item in payload.items():
            if not isinstance(item, dict):
                continue
            pricing[str(model_name)] = {
                "input_per_1k": max(float(item.get("input_per_1k") or 0), 0.0),
                "output_per_1k": max(float(item.get("output_per_1k") or 0), 0.0),
            }
        return currency, pricing

    def get_model_pricing(self) -> dict:
        currency, pricing = self._parse_model_pricing()
        items = [
            {
                "model_name": model_name,
                "currency": currency,
                "input_per_1k": config["input_per_1k"],
                "output_per_1k": config["output_per_1k"],
            }
            for model_name, config in sorted(pricing.items(), key=lambda item: item[0])
        ]
        return {
            "currency": currency,
            "items": items,
        }

    def _compute_llm_call_costs(self, rows: list[LLMCallLog]) -> tuple[list[dict], dict]:
        currency, pricing = self._parse_model_pricing()
        items = []
        total_input_cost = 0.0
        total_output_cost = 0.0
        metered_calls = 0
        unmapped_models: set[str] = set()

        for row in rows:
            model_name = row.model_name or "unknown"
            price = pricing.get(model_name)
            input_tokens = int(row.input_tokens or 0)
            output_tokens = int(row.output_tokens or 0)
            input_cost = 0.0
            output_cost = 0.0
            total_cost = 0.0
            priced = False
            if price:
                input_cost = input_tokens / 1000 * float(price["input_per_1k"])
                output_cost = output_tokens / 1000 * float(price["output_per_1k"])
                total_cost = input_cost + output_cost
                priced = True
                metered_calls += 1
                total_input_cost += input_cost
                total_output_cost += output_cost
            else:
                unmapped_models.add(model_name)

            items.append(
                {
                    "id": row.id,
                    "module_name": row.module_name,
                    "action": row.action,
                    "model_name": model_name,
                    "prompt_template": row.prompt_template,
                    "prompt_version": row.prompt_version,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_ms": row.duration_ms,
                    "status": row.status,
                    "created_at": row.created_at,
                    "priced": priced,
                    "currency": currency,
                    "input_cost": round(input_cost, 6),
                    "output_cost": round(output_cost, 6),
                    "total_cost": round(total_cost, 6),
                }
            )

        summary = {
            "currency": currency,
            "metered_calls": metered_calls,
            "unpriced_calls": max(len(rows) - metered_calls, 0),
            "total_input_cost": round(total_input_cost, 6),
            "total_output_cost": round(total_output_cost, 6),
            "total_cost": round(total_input_cost + total_output_cost, 6),
            "unmapped_models": sorted(unmapped_models),
        }
        return items, summary

    def get_llm_billing_stats(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        module_name: str | None = None,
        action: str | None = None,
        status: str | None = None,
    ) -> dict:
        rows = self.list_llm_calls(
            db=db,
            user_id=user_id,
            include_all_users=include_all_users,
            days=days,
            limit=5000,
            module_name=module_name,
            action=action,
            status=status,
        )
        cost_items, summary = self._compute_llm_call_costs(rows)
        by_model: dict[str, dict] = {}
        by_date: dict[str, dict] = {}
        by_action: dict[str, dict] = {}
        for item in cost_items:
            model_name = item["model_name"]
            date_key = item["created_at"].strftime("%Y-%m-%d") if item["created_at"] else "unknown"
            action_key = item["action"] or "unknown"
            model_entry = by_model.setdefault(
                model_name,
                {
                    "calls": 0,
                    "priced_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_cost": 0.0,
                },
            )
            model_entry["calls"] += 1
            model_entry["priced_calls"] += 1 if item["priced"] else 0
            model_entry["input_tokens"] += item["input_tokens"]
            model_entry["output_tokens"] += item["output_tokens"]
            model_entry["total_cost"] = round(model_entry["total_cost"] + item["total_cost"], 6)

            action_entry = by_action.setdefault(
                action_key,
                {
                    "calls": 0,
                    "priced_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_cost": 0.0,
                    "by_model": {},
                },
            )
            action_entry["calls"] += 1
            action_entry["priced_calls"] += 1 if item["priced"] else 0
            action_entry["input_tokens"] += item["input_tokens"]
            action_entry["output_tokens"] += item["output_tokens"]
            action_entry["total_cost"] = round(action_entry["total_cost"] + item["total_cost"], 6)
            action_model = action_entry["by_model"].setdefault(
                model_name,
                {"calls": 0, "priced_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_cost": 0.0},
            )
            action_model["calls"] += 1
            action_model["priced_calls"] += 1 if item["priced"] else 0
            action_model["input_tokens"] += item["input_tokens"]
            action_model["output_tokens"] += item["output_tokens"]
            action_model["total_cost"] = round(action_model["total_cost"] + item["total_cost"], 6)

            date_entry = by_date.setdefault(
                date_key,
                {
                    "calls": 0,
                    "priced_calls": 0,
                    "total_cost": 0.0,
                },
            )
            date_entry["calls"] += 1
            date_entry["priced_calls"] += 1 if item["priced"] else 0
            date_entry["total_cost"] = round(date_entry["total_cost"] + item["total_cost"], 6)

        for action_entry in by_action.values():
            action_entry["avg_cost_per_call"] = round(
                action_entry["total_cost"] / action_entry["calls"], 6
            ) if action_entry["calls"] else 0.0

        return {
            "days": days,
            "summary": summary,
            "by_model": by_model,
            "by_date": by_date,
            "by_action": by_action,
            "pricing": self.get_model_pricing(),
        }

    def get_llm_routing_stats(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
    ) -> dict:
        rows = self.list_llm_calls(
            db=db,
            user_id=user_id,
            include_all_users=include_all_users,
            days=days,
            limit=5000,
        )
        routed_rows = [row for row in rows if row.request_id and row.routing_role and row.routing_stage]
        requests: dict[str, list[LLMCallLog]] = {}
        for row in routed_rows:
            requests.setdefault(row.request_id, []).append(row)

        total_requests = len(requests)
        small_initial_hits = 0
        primary_initial_requests = 0
        primary_failures = 0
        fallback_requests = 0
        fallback_successes = 0
        by_action: dict[str, dict] = {}
        currency, pricing = self._parse_model_pricing()
        model_costs = {"primary": 0.0, "small": 0.0}

        for request_rows in requests.values():
            initial = next((row for row in request_rows if row.routing_stage == "initial"), None)
            fallback = next((row for row in request_rows if row.routing_stage == "fallback"), None)
            if not initial:
                continue
            action = initial.action or "unknown"
            action_entry = by_action.setdefault(
                action,
                {"requests": 0, "fallback_requests": 0, "successful_requests": 0, "avg_attempt_latency_ms": 0, "attempts": 0, "total_attempt_latency_ms": 0},
            )
            action_entry["requests"] += 1
            if initial.routing_role == "small":
                small_initial_hits += 1
            else:
                primary_initial_requests += 1
                if initial.status != "success":
                    primary_failures += 1
            if fallback:
                fallback_requests += 1
                action_entry["fallback_requests"] += 1
                if fallback.status == "success":
                    fallback_successes += 1
            if any(row.status == "success" for row in request_rows):
                action_entry["successful_requests"] += 1
            for row in request_rows:
                action_entry["attempts"] += 1
                action_entry["total_attempt_latency_ms"] += int(row.duration_ms or 0)
                price = pricing.get(row.model_name or "")
                if price and row.routing_role in model_costs:
                    model_costs[row.routing_role] += (
                        int(row.input_tokens or 0) / 1000 * float(price["input_per_1k"])
                        + int(row.output_tokens or 0) / 1000 * float(price["output_per_1k"])
                    )

        for item in by_action.values():
            attempts = item.pop("attempts")
            total_latency = item.pop("total_attempt_latency_ms")
            item["avg_attempt_latency_ms"] = round(total_latency / attempts) if attempts else 0
            item["success_rate"] = round(item["successful_requests"] / item["requests"], 4) if item["requests"] else 0

        total_cost = model_costs["primary"] + model_costs["small"]
        return {
            "days": days,
            "routed_requests": total_requests,
            "untracked_calls": max(len(rows) - len(routed_rows), 0),
            "small_model_initial_hits": small_initial_hits,
            "small_model_hit_rate": round(small_initial_hits / total_requests, 4) if total_requests else 0,
            "primary_initial_requests": primary_initial_requests,
            "primary_failure_count": primary_failures,
            "primary_failure_rate": round(primary_failures / primary_initial_requests, 4) if primary_initial_requests else 0,
            "fallback_request_count": fallback_requests,
            "fallback_success_count": fallback_successes,
            "fallback_success_rate": round(fallback_successes / fallback_requests, 4) if fallback_requests else 0,
            "cost": {
                "currency": currency,
                "primary_cost": round(model_costs["primary"], 6),
                "small_cost": round(model_costs["small"], 6),
                "total_cost": round(total_cost, 6),
                "small_model_cost_share": round(model_costs["small"] / total_cost, 4) if total_cost else 0,
            },
            "by_action": by_action,
        }

    def get_llm_routing_health(self, db: Session, *, hours: int = 1) -> dict:
        """Assess recent routing reliability without exposing prompts or provider errors."""
        since = utc_now() - timedelta(hours=hours)
        rows = (
            db.query(LLMCallLog)
            .filter(
                LLMCallLog.created_at >= since,
                LLMCallLog.request_id.isnot(None),
                LLMCallLog.routing_role.isnot(None),
                LLMCallLog.routing_stage.isnot(None),
            )
            .order_by(LLMCallLog.created_at.desc())
            .limit(5000)
            .all()
        )
        requests: dict[str, list[LLMCallLog]] = {}
        for row in rows:
            requests.setdefault(row.request_id, []).append(row)

        total_requests = len(requests)
        primary_initial_requests = 0
        primary_failures = 0
        fallback_requests = 0
        fallback_failures = 0
        for request_rows in requests.values():
            initial = next((row for row in request_rows if row.routing_stage == "initial"), None)
            fallback = next((row for row in request_rows if row.routing_stage == "fallback"), None)
            if initial and initial.routing_role == "primary":
                primary_initial_requests += 1
                primary_failures += int(initial.status != "success")
            if fallback:
                fallback_requests += 1
                fallback_failures += int(fallback.status != "success")

        primary_failure_rate = primary_failures / primary_initial_requests if primary_initial_requests else 0.0
        fallback_failure_rate = fallback_failures / fallback_requests if fallback_requests else 0.0
        warnings = []
        if total_requests >= settings.LLM_ROUTING_ALERT_MIN_REQUESTS:
            if primary_initial_requests and primary_failure_rate >= settings.LLM_ROUTING_ALERT_PRIMARY_FAILURE_RATE:
                warnings.append("primary_failure_rate_high")
            if fallback_requests and fallback_failure_rate >= settings.LLM_ROUTING_ALERT_FALLBACK_FAILURE_RATE:
                warnings.append("fallback_failure_rate_high")

        return {
            "status": "degraded" if warnings else "ok",
            "window_hours": hours,
            "routed_requests": total_requests,
            "primary_initial_requests": primary_initial_requests,
            "primary_failure_rate": round(primary_failure_rate, 4),
            "fallback_requests": fallback_requests,
            "fallback_failure_rate": round(fallback_failure_rate, 4),
            "minimum_requests": settings.LLM_ROUTING_ALERT_MIN_REQUESTS,
            "warnings": warnings,
        }

    def list_qa_replays(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        page: int = 1,
        page_size: int = 20,
        source: str | None = None,
        feedback_status: str | None = None,
    ) -> dict:
        since = utc_now() - timedelta(days=days)
        query = db.query(DocumentQARecord).filter(DocumentQARecord.created_at >= since)
        if user_id is not None and not include_all_users:
            query = query.filter(DocumentQARecord.user_id == user_id)
        if source:
            query = query.filter(DocumentQARecord.source == source)
        if feedback_status:
            query = query.filter(DocumentQARecord.feedback_status == feedback_status)
        total = query.count()
        rows = (
            query.order_by(DocumentQARecord.created_at.desc(), DocumentQARecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [document_qa_service.serialize_record(row) for row in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def list_feedback_records(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        limit: int = 200,
        feedback_value: str | None = None,
        feedback_status: str | None = None,
        source: str | None = None,
    ) -> list[DocumentQARecord]:
        since = utc_now() - timedelta(days=days)
        query = db.query(DocumentQARecord).filter(
            DocumentQARecord.feedback_created_at.isnot(None),
            DocumentQARecord.feedback_created_at >= since,
        )
        if user_id is not None and not include_all_users:
            query = query.filter(DocumentQARecord.user_id == user_id)
        if feedback_value:
            query = query.filter(DocumentQARecord.feedback_value == feedback_value)
        if feedback_status:
            query = query.filter(DocumentQARecord.feedback_status == feedback_status)
        if source:
            query = query.filter(DocumentQARecord.source == source)
        return (
            query.order_by(DocumentQARecord.feedback_created_at.desc(), DocumentQARecord.id.desc())
            .limit(limit)
            .all()
        )

    def get_feedback_stats(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        feedback_value: str | None = None,
        feedback_status: str | None = None,
        source: str | None = None,
    ) -> dict:
        rows = self.list_feedback_records(
            db=db,
            user_id=user_id,
            include_all_users=include_all_users,
            days=days,
            limit=2000,
            feedback_value=feedback_value,
            feedback_status=feedback_status,
            source=source,
        )

        by_value: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        by_source: dict[str, int] = {}
        by_date: dict[str, int] = {}
        resolved_count = 0
        open_count = 0
        negative_resolved_count = 0

        for row in rows:
            value_key = row.feedback_value or "unknown"
            status_key = row.feedback_status or "unknown"
            source_key = row.source or "unknown"
            date_key = row.feedback_created_at.strftime("%Y-%m-%d") if row.feedback_created_at else "unknown"

            by_value[value_key] = by_value.get(value_key, 0) + 1
            by_status[status_key] = by_status.get(status_key, 0) + 1
            by_source[source_key] = by_source.get(source_key, 0) + 1
            by_date[date_key] = by_date.get(date_key, 0) + 1
            if row.feedback_reason:
                by_reason[row.feedback_reason] = by_reason.get(row.feedback_reason, 0) + 1
            if row.feedback_status == "resolved":
                resolved_count += 1
                if row.feedback_value == "negative":
                    negative_resolved_count += 1
            if row.feedback_status == "open":
                open_count += 1

        total = len(rows)
        negative_count = by_value.get("negative", 0)
        positive_count = by_value.get("positive", 0)
        return {
            "days": days,
            "total_feedback": total,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "open_count": open_count,
            "resolved_count": resolved_count,
            "positive_rate": round(positive_count / total, 4) if total else 0,
            "resolution_rate": round(negative_resolved_count / negative_count, 4) if negative_count else 0,
            "by_value": by_value,
            "by_status": by_status,
            "by_reason": by_reason,
            "by_source": by_source,
            "by_date": by_date,
        }

    def list_alerts(
        self,
        db: Session,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        limit: int = 100,
        source: str | None = None,
        category: str | None = None,
        severity: str | None = None,
    ) -> list[dict]:
        since = utc_now() - timedelta(days=days)

        async_task_query = db.query(OperationLog).filter(
            OperationLog.module == "async_task",
            OperationLog.created_at >= since,
            OperationLog.action.like("%_failed"),
        )
        agent_query = db.query(AgentRun).filter(
            AgentRun.created_at >= since,
            AgentRun.status == "error",
        )

        if user_id is not None and not include_all_users:
            async_task_query = async_task_query.filter(OperationLog.user_id == user_id)
            agent_query = agent_query.filter(AgentRun.user_id == user_id)

        outbound_failure_query = db.query(EmailSendRequest).filter(
            EmailSendRequest.created_at >= since,
            EmailSendRequest.status == "failed",
        )
        outbound_pending_query = db.query(EmailSendRequest).filter(
            EmailSendRequest.created_at >= since,
            EmailSendRequest.created_at < utc_now() - timedelta(hours=24),
            EmailSendRequest.status == "pending",
        )
        if user_id is not None and not include_all_users:
            outbound_failure_query = outbound_failure_query.filter(EmailSendRequest.user_id == user_id)
            outbound_pending_query = outbound_pending_query.filter(EmailSendRequest.user_id == user_id)

        query_limit = max(limit * 5, 200)
        async_task_logs = async_task_query.order_by(OperationLog.created_at.desc()).limit(query_limit).all()
        agent_runs = agent_query.order_by(AgentRun.created_at.desc()).limit(query_limit).all()
        outbound_failures = outbound_failure_query.order_by(EmailSendRequest.created_at.desc()).limit(query_limit).all()
        outbound_pending = outbound_pending_query.order_by(EmailSendRequest.created_at.asc()).limit(query_limit).all()

        alerts: list[dict] = []
        for log in async_task_logs:
            alert = {
                "source": "async_task",
                "title": log.action,
                "message": log.detail or "异步任务执行失败",
                "user_id": log.user_id,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "created_at": log.created_at,
            }
            alert.update(
                _classify_alert(
                    source=alert["source"],
                    title=alert["title"],
                    message=alert["message"],
                    target_type=alert["target_type"],
                )
            )
            alerts.append(
                alert
            )

        for run in agent_runs:
            alert = {
                "source": "agent",
                "title": "agent_run_failed",
                "message": run.failure_reason or run.error or run.goal[:120],
                "user_id": run.user_id,
                "target_type": "agent_run",
                "target_id": run.id,
                "created_at": run.created_at,
            }
            alert.update(
                _classify_alert(
                    source=alert["source"],
                    title=alert["title"],
                    message=alert["message"],
                    target_type=alert["target_type"],
                )
            )
            alerts.append(
                alert
            )

        for request in outbound_failures:
            alert = {
                "source": "outbound_email", "title": "smtp_delivery_failed",
                "message": "SMTP 发送失败，请检查发信连接与发送策略。",
                "user_id": request.user_id, "target_type": "email_send_request",
                "target_id": request.id, "created_at": request.updated_at or request.created_at,
            }
            alert.update(_classify_alert(source=alert["source"], title=alert["title"], message=alert["message"], target_type=alert["target_type"]))
            alerts.append(alert)

        for request in outbound_pending:
            alert = {
                "source": "outbound_email", "title": "outbound_approval_pending",
                "message": "发信申请已等待审批超过 24 小时。",
                "user_id": request.user_id, "target_type": "email_send_request",
                "target_id": request.id, "created_at": request.created_at,
            }
            alert.update(_classify_alert(source=alert["source"], title=alert["title"], message=alert["message"], target_type=alert["target_type"]))
            alerts.append(alert)

        if source:
            alerts = [item for item in alerts if item["source"] == source]
        if category:
            alerts = [item for item in alerts if item["category"] == category]
        if severity:
            alerts = [item for item in alerts if item["severity"] == severity]

        alerts.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)
        return alerts[:limit]

    def get_alert_stats(
        self,
        db: Session,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        source: str | None = None,
        category: str | None = None,
        severity: str | None = None,
    ) -> dict:
        alerts = self.list_alerts(
            db=db,
            user_id=user_id,
            include_all_users=include_all_users,
            days=days,
            limit=1000,
            source=source,
            category=category,
            severity=severity,
        )
        return _build_alert_stats(alerts)

    def list_operation_logs(
        self,
        db: Session,
        user_id: int | None = None,
        module: str | None = None,
        include_all_users: bool = False,
        days: int = 30,
        limit: int = 200,
    ) -> list[OperationLog]:
        since = utc_now() - timedelta(days=days)
        query = db.query(OperationLog).filter(OperationLog.created_at >= since)
        if user_id is not None and not include_all_users:
            query = query.filter(OperationLog.user_id == user_id)
        if module:
            query = query.filter(OperationLog.module == module)
        return query.order_by(OperationLog.created_at.desc()).limit(limit).all()

    def get_operation_stats(self, user_id: int, db: Session, days: int = 30) -> dict:
        since = utc_now() - timedelta(days=days)
        rows = db.query(OperationLog).filter(
            OperationLog.user_id == user_id,
            OperationLog.created_at >= since,
        ).all()

        by_module = {}
        for row in rows:
            key = row.module
            if key not in by_module:
                by_module[key] = 0
            by_module[key] += 1

        return {
            "total_operations": len(rows),
            "by_module": by_module,
        }

    def create_operation_log(
        self,
        module: str,
        action: str,
        db: Session,
        user_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        detail: str | None = None,
        ip_address: str | None = None,
    ) -> OperationLog:
        entry = OperationLog(
            user_id=user_id,
            module=module,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
            ip_address=ip_address,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    @staticmethod
    def _load_json_artifact(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _artifact_meta(path: Path) -> dict:
        if not path.exists():
            return {
                "exists": False,
                "path": str(path),
                "updated_at": None,
            }
        return {
            "exists": True,
            "path": str(path),
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        }

    def get_prompt_rollout_overview(self, db: Session) -> dict:
        rows = db.query(PromptTemplate).order_by(PromptTemplate.updated_at.desc(), PromptTemplate.id.desc()).all()
        items = []
        active_rollout_count = 0
        for row in rows:
            rollout = None
            if row.rollout_version_id and row.rollout_version and int(row.rollout_percentage or 0) > 0:
                active_rollout_count += 1
                rollout = {
                    "version_id": row.rollout_version.id,
                    "version_number": row.rollout_version.version,
                    "percentage": int(row.rollout_percentage or 0),
                    "started_at": row.rollout_started_at,
                }
            items.append(
                {
                    "template_id": row.id,
                    "name": row.name,
                    "active_version_id": row.active_version_id,
                    "active_version_number": row.active_version.version if row.active_version else None,
                    "previous_active_version_id": row.previous_active_version_id,
                    "previous_active_version_number": row.previous_active_version.version if row.previous_active_version else None,
                    "rollout": rollout,
                    "updated_at": row.updated_at,
                }
            )
        return {
            "total_templates": len(items),
            "active_rollout_count": active_rollout_count,
            "items": items,
        }

    def get_prompt_traffic_overview(self, db: Session, days: int = 30, limit: int = 100) -> dict:
        since = utc_now() - timedelta(days=days)
        rows = (
            db.query(LLMCallLog)
            .filter(
                LLMCallLog.created_at >= since,
                LLMCallLog.prompt_template.isnot(None),
                LLMCallLog.prompt_version.isnot(None),
            )
            .order_by(LLMCallLog.created_at.desc(), LLMCallLog.id.desc())
            .limit(5000)
            .all()
        )
        by_prompt: dict[tuple[str, int], dict] = {}
        for row in rows:
            key = (row.prompt_template or "unknown", int(row.prompt_version or 0))
            item = by_prompt.setdefault(
                key,
                {
                    "prompt_template": key[0],
                    "prompt_version": key[1],
                    "calls": 0,
                    "failed_calls": 0,
                    "last_called_at": row.created_at,
                },
            )
            item["calls"] += 1
            if row.status != "success":
                item["failed_calls"] += 1
            if row.created_at and (item["last_called_at"] is None or row.created_at > item["last_called_at"]):
                item["last_called_at"] = row.created_at
        items = sorted(by_prompt.values(), key=lambda item: (item["calls"], item["last_called_at"] or datetime.min), reverse=True)
        return {
            "days": days,
            "total_prompt_versions": len(items),
            "items": items[:limit],
        }

    def get_experiment_overview(self, db: Session, days: int = 30) -> dict:
        output_dir = DEFAULT_OUTPUT_DIR
        summary_path = output_dir / "summary.json"
        baseline_path = DEFAULT_BASELINE_SNAPSHOT_PATH
        summary_payload = self._load_json_artifact(summary_path)
        baseline_payload = self._load_json_artifact(baseline_path)
        summary_rows = summary_payload.get("experiments") if isinstance(summary_payload.get("experiments"), list) else []
        baseline_config = ((baseline_payload.get("baseline") or {}).get("effective_config") or {}) if baseline_payload else {}

        experiments = []
        degraded_experiment_count = 0
        for row in summary_rows:
            effective_config = row.get("effective_config") or {}
            summary = row.get("summary") or {}
            baseline_delta = row.get("baseline_delta") or {}
            regression_metrics = []
            for metric in ("hit_at_k", "citation_accuracy", "refusal_accuracy"):
                delta = baseline_delta.get(metric)
                if delta is not None and delta < 0:
                    regression_metrics.append(metric)
            badcase_delta = baseline_delta.get("badcase_count")
            if badcase_delta is not None and badcase_delta > 0:
                regression_metrics.append("badcase_count")
            config_drift = []
            for field in (
                "top_k",
                "confidence_threshold",
                "min_recall_candidates",
                "recall_multiplier",
                "query_variant_limit",
                "context_neighbor_window",
                "context_max_chunks",
                "prompt_template",
                "prompt_version",
            ):
                if baseline_config and effective_config.get(field) != baseline_config.get(field):
                    config_drift.append(
                        {
                            "field": field,
                            "baseline": baseline_config.get(field),
                            "current": effective_config.get(field),
                        }
                    )
            if regression_metrics:
                degraded_experiment_count += 1
            experiments.append(
                {
                    "name": row.get("name") or "unnamed",
                    "effective_config": effective_config,
                    "summary": summary,
                    "baseline_delta": baseline_delta,
                    "badcase_count": int(row.get("badcase_count") or 0),
                    "badcase_path": row.get("badcase_path"),
                    "regression_metrics": regression_metrics,
                    "config_drift": config_drift,
                }
            )

        experiments.sort(
            key=lambda item: (
                -float((item.get("summary") or {}).get("citation_accuracy") or 0),
                -float((item.get("summary") or {}).get("hit_at_k") or 0),
                item.get("name") or "",
            )
        )
        rollout_overview = self.get_prompt_rollout_overview(db)
        prompt_traffic = self.get_prompt_traffic_overview(db, days=days, limit=50)
        return {
            "artifact_status": {
                "output_dir": str(output_dir),
                "summary": self._artifact_meta(summary_path),
                "baseline_snapshot": self._artifact_meta(baseline_path),
            },
            "summary": {
                "dataset_size": int(summary_payload.get("dataset_size") or 0),
                "experiment_count": int(summary_payload.get("experiment_count") or len(experiments)),
                "baseline_experiment": summary_payload.get("baseline_experiment"),
                "bundle_meta": summary_payload.get("bundle_meta") or {},
                "baseline_snapshot": baseline_payload.get("baseline") or {},
                "degraded_experiment_count": degraded_experiment_count,
            },
            "experiments": experiments,
            "rollouts": rollout_overview,
            "prompt_traffic": prompt_traffic,
        }

    def get_tool_health(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
    ) -> dict:
        since = utc_now() - timedelta(days=days)
        query = db.query(ToolCallLog).join(AgentRun).filter(ToolCallLog.created_at >= since)
        if user_id is not None and not include_all_users:
            query = query.filter(AgentRun.user_id == user_id)
        rows = query.order_by(ToolCallLog.created_at.desc(), ToolCallLog.id.desc()).all()
        by_tool: dict[str, dict] = {}
        for row in rows:
            key = row.tool_name or "unknown"
            item = by_tool.setdefault(
                key,
                {
                    "tool_name": key,
                    "calls": 0,
                    "success_calls": 0,
                    "failed_calls": 0,
                    "pending_approval_calls": 0,
                    "avg_duration_ms": 0,
                    "total_duration_ms": 0,
                    "last_called_at": row.created_at,
                    "last_error": None,
                },
            )
            item["calls"] += 1
            if row.status == "success":
                item["success_calls"] += 1
            elif row.status == "pending_approval":
                item["pending_approval_calls"] += 1
            else:
                item["failed_calls"] += 1
            item["total_duration_ms"] += int(row.duration_ms or 0)
            if row.created_at and (item["last_called_at"] is None or row.created_at > item["last_called_at"]):
                item["last_called_at"] = row.created_at
            if row.error and not item["last_error"]:
                item["last_error"] = row.error
        items = []
        for item in by_tool.values():
            calls = max(int(item["calls"] or 0), 1)
            item["avg_duration_ms"] = round(item["total_duration_ms"] / calls)
            item["success_rate"] = round((item["success_calls"] / calls), 4)
            item.pop("total_duration_ms", None)
            items.append(item)
        items.sort(key=lambda item: (item["failed_calls"], item["pending_approval_calls"], item["calls"]), reverse=True)
        return {
            "days": days,
            "total_tools": len(items),
            "items": items,
        }

    def export_feedback_eval_bundle(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
    ) -> dict:
        rows = self.list_feedback_records(
            db=db,
            user_id=user_id,
            include_all_users=include_all_users,
            days=days,
            limit=1000,
            feedback_value="negative",
            feedback_status=None,
            source=None,
        )
        if settings.EVAL_BUNDLE_OUTPUT_DIR:
            bundle_dir = Path(settings.EVAL_BUNDLE_OUTPUT_DIR)
        else:
            bundle_dir = Path("eval") / "bundles" / "feedback_autogen"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        dataset_path = bundle_dir / "qa_dataset.json"
        items = []
        for row in rows:
            citations = document_qa_service.serialize_record(row).get("citations") or []
            items.append(
                {
                    "qa_record_id": row.id,
                    "document_id": row.document_id,
                    "document_title": row.document.title if row.document else None,
                    "question": row.question,
                    "previous_answer": row.answer,
                    "feedback_reason": row.feedback_reason,
                    "feedback_note": row.feedback_note,
                    "citations": citations,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
            )
        dataset_path.write_text(
            json.dumps(
                {
                    "generated_at": utc_now().isoformat(),
                    "days": days,
                    "count": len(items),
                    "items": items,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "bundle_dir": str(bundle_dir),
            "dataset_path": str(dataset_path),
            "count": len(items),
        }

    def get_user_token_stats(self, user_id: int, db: Session, days: int = 30) -> dict:
        since = utc_now() - timedelta(days=days)
        rows = db.query(TokenUsage).filter(
            TokenUsage.user_id == user_id,
            TokenUsage.created_at >= since,
        ).all()

        total_calls = len(rows)
        total_prompt = sum(row.prompt_tokens for row in rows)
        total_completion = sum(row.completion_tokens for row in rows)
        total_tokens = sum(row.total_tokens for row in rows)
        total_duration = sum(row.duration_ms or 0 for row in rows)

        by_action = {}
        for row in rows:
            key = row.action or "unknown"
            if key not in by_action:
                by_action[key] = {"calls": 0, "total_tokens": 0}
            by_action[key]["calls"] += 1
            by_action[key]["total_tokens"] += row.total_tokens

        by_date = {}
        for row in rows:
            key = row.created_at.strftime("%Y-%m-%d") if row.created_at else "unknown"
            if key not in by_date:
                by_date[key] = {"calls": 0, "total_tokens": 0}
            by_date[key]["calls"] += 1
            by_date[key]["total_tokens"] += row.total_tokens

        return {
            "days": days,
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "avg_duration_ms": round(total_duration / total_calls) if total_calls else 0,
            "by_action": by_action,
            "by_date": by_date,
            "governance": llm_governance_service.get_user_status(db, user_id),
        }

    def get_global_token_stats(self, db: Session, days: int = 30) -> dict:
        since = utc_now() - timedelta(days=days)
        rows = db.query(TokenUsage).filter(TokenUsage.created_at >= since).all()

        total_calls = len(rows)
        total_tokens = sum(row.total_tokens for row in rows)
        total_prompt = sum(row.prompt_tokens for row in rows)
        total_completion = sum(row.completion_tokens for row in rows)

        by_model = {}
        for row in rows:
            key = row.model
            if key not in by_model:
                by_model[key] = {"calls": 0, "total_tokens": 0}
            by_model[key]["calls"] += 1
            by_model[key]["total_tokens"] += row.total_tokens

        by_date = {}
        for row in rows:
            key = row.created_at.strftime("%Y-%m-%d") if row.created_at else "unknown"
            if key not in by_date:
                by_date[key] = {"calls": 0, "total_tokens": 0}
            by_date[key]["calls"] += 1
            by_date[key]["total_tokens"] += row.total_tokens

        return {
            "days": days,
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "by_model": by_model,
            "by_date": by_date,
            "governance": llm_governance_service.get_global_status(db),
        }

    def list_task_runs(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        limit: int = 100,
        source: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        since = utc_now() - timedelta(days=days)
        items: list[dict] = []

        if source in (None, "async_task"):
            query = db.query(OperationLog).filter(
                OperationLog.module == "async_task",
                OperationLog.created_at >= since,
            )
            if user_id is not None and not include_all_users:
                query = query.filter(OperationLog.user_id == user_id)

            logs = query.order_by(OperationLog.created_at.desc()).limit(limit * 10).all()
            grouped: dict[str, dict] = {}

            for log in logs:
                task_id = extract_task_id(log.detail)
                if not task_id:
                    continue
                entry = grouped.get(task_id)
                if not entry:
                    result = celery_app.AsyncResult(task_id)
                    celery_state = result.state
                    normalized_status = normalize_async_state(celery_state, log.action)
                    detail = log.detail or ""
                    entry = {
                        "task_key": task_id,
                        "source": "async_task",
                        "task_type": log.action.rsplit("_", 1)[0],
                        "title": _task_title_from_action(log.action, log.target_type),
                        "status": normalized_status,
                        "celery_state": celery_state,
                        "module": "async_task",
                        "target_type": log.target_type,
                        "target_id": log.target_id,
                        "user_id": log.user_id,
                        "message": detail,
                        "error": str(result.info) if result.failed() else None,
                        "retryable": normalized_status == "failed",
                        "created_at": log.created_at,
                        "updated_at": log.created_at,
                        "events": [],
                    }
                    if result.failed() and not entry["error"]:
                        entry["error"] = detail
                    if result.successful() and isinstance(result.info, dict):
                        entry["result"] = result.info
                    grouped[task_id] = entry

                entry["events"].append(
                    {
                        "id": log.id,
                        "action": log.action,
                        "detail": log.detail,
                        "created_at": log.created_at,
                    }
                )

                if log.created_at and log.created_at > entry["updated_at"]:
                    entry["updated_at"] = log.created_at
                    entry["message"] = log.detail or entry["message"]

                action_status = normalize_async_state(None, log.action)
                if action_status == "failed":
                    entry["status"] = "failed"
                    entry["error"] = log.detail or entry["error"]
                elif entry["status"] not in {"failed", "succeeded"}:
                    entry["status"] = action_status

            items.extend(grouped.values())

        if source in (None, "agent"):
            query = db.query(AgentRun).filter(AgentRun.created_at >= since)
            if user_id is not None and not include_all_users:
                query = query.filter(AgentRun.user_id == user_id)
            runs = query.order_by(AgentRun.created_at.desc()).limit(limit).all()
            for run in runs:
                normalized_status = {
                    "running": "running",
                    "completed": "succeeded",
                    "error": "failed",
                }.get(run.status, run.status or "pending")
                items.append(
                    {
                        "task_key": str(run.id),
                        "source": "agent",
                        "task_type": "agent_run",
                        "title": "Agent 执行",
                        "status": normalized_status,
                        "celery_state": None,
                        "module": "agent",
                        "target_type": "agent_run",
                        "target_id": run.id,
                        "user_id": run.user_id,
                        "message": run.final_answer or run.result or run.goal,
                        "error": run.failure_reason or run.error,
                        "retryable": normalized_status == "failed",
                        "created_at": run.created_at,
                        "updated_at": run.completed_at or run.created_at,
                        "goal": run.goal,
                        "total_steps": run.total_steps,
                    }
                )

        if status:
            items = [item for item in items if item["status"] == status]

        items.sort(key=lambda item: item["updated_at"] or item["created_at"] or datetime.min, reverse=True)
        return items[:limit]

    def retry_task_run(
        self,
        db: Session,
        *,
        source: str,
        task_key: str,
        user_id: int,
    ) -> dict:
        if source == "agent":
            run = db.query(AgentRun).filter(AgentRun.id == int(task_key), AgentRun.user_id == user_id).first()
            if not run:
                raise ValueError("Agent run not found")
            return {
                "source": "agent",
                "task_key": task_key,
                "goal": run.goal,
                "max_steps": run.total_steps or 5,
            }

        if source != "async_task":
            raise ValueError("Unsupported task source")

        logs = (
            db.query(OperationLog)
            .filter(
                OperationLog.module == "async_task",
                OperationLog.user_id == user_id,
                OperationLog.detail.like(f"%task_id={task_key}%"),
            )
            .order_by(OperationLog.created_at.desc())
            .all()
        )
        if not logs:
            raise ValueError("Async task not found")

        latest = logs[0]
        action = latest.action or ""
        target_type = latest.target_type
        target_id = latest.target_id
        max_length = extract_max_length(latest.detail)

        if action.startswith("document_analysis"):
            task = analyze_document_task.delay(int(target_id), user_id, max_length)
        elif action.startswith("document_summary"):
            task = summarize_document_task.delay(int(target_id), user_id, max_length)
        elif action.startswith("document_parse"):
            doc = db.query(Document).filter(Document.id == int(target_id), Document.user_id == user_id).first()
            if not doc:
                raise ValueError("Document not found")
            task = parse_document_task.delay(doc.id, doc.version_number, doc.file_type)
        else:
            raise ValueError("Task type is not retryable")

        self.create_operation_log(
            module="async_task",
            action=f"{action.rsplit('_', 1)[0]}_submitted",
            db=db,
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
            detail=f"task_id={task.id}; retry_of={task_key}; max_length={max_length}",
        )

        return {
            "source": "async_task",
            "task_key": task.id,
            "retry_of": task_key,
            "target_type": target_type,
            "target_id": target_id,
        }


analytics_service = AnalyticsService()
