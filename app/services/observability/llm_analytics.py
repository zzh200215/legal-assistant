"""LLM 用量分析簇：调用列表/统计、计价、计费、路由统计与工具健康。"""
import json
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.agent import AgentRun, ToolCallLog
from app.models.llm_call_log import LLMCallLog
from app.models.token_usage import TokenUsage
from app.services.llm.llm_governance_service import llm_governance_service

settings = get_settings()


class LLMAnalyticsMixin:
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
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[LLMCallLog] | tuple[list[LLMCallLog], int]:
        """列表查询。page/page_size 同时提供时走 DB offset/limit + count，
        返回 ``(items, total)``；否则保持全量 ``limit`` 语义返回 list。
        """
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
        ordered = query.order_by(LLMCallLog.created_at.desc(), LLMCallLog.id.desc())
        if page is not None and page_size is not None:
            total = query.count()
            items = ordered.offset((page - 1) * page_size).limit(page_size).all()
            return items, total
        return ordered.limit(limit).all()

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

