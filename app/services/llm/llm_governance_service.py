from __future__ import annotations

import json
import math
import time
from datetime import datetime
from threading import Lock
from typing import Any

import redis
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.model_policy import get_task_policy
from app.core.time import utc_now
from app.models.llm_call_log import LLMCallLog
from app.models.token_usage import TokenUsage
from app.services.llm.llm_observability_service import llm_observability_service

settings = get_settings()


class LLMGovernanceError(HTTPException):
    def __init__(self, *, status_code: int, code: str, message: str, detail=None):
        self.code = code
        payload = {
            "code": code,
            "message": message,
            "detail": detail if detail is not None else message,
        }
        super().__init__(status_code=status_code, detail=payload)


_fallback_rate_counters: dict[str, tuple[int, float]] = {}
_fallback_rate_lock = Lock()


class LLMGovernanceService:
    def __init__(self) -> None:
        self._redis_client: redis.Redis | None = None
        self._redis_unavailable = False

    def get_policy(self) -> dict[str, int]:
        return {
            "rate_limit_window_seconds": max(0, int(settings.LLM_RATE_LIMIT_WINDOW_SECONDS)),
            "rate_limit_max_requests": max(0, int(settings.LLM_RATE_LIMIT_MAX_REQUESTS)),
            "daily_request_limit": max(0, int(settings.LLM_DAILY_REQUEST_LIMIT)),
            "daily_token_limit": max(0, int(settings.LLM_DAILY_TOKEN_LIMIT)),
            "estimated_chars_per_token": max(1, int(settings.LLM_ESTIMATED_CHARS_PER_TOKEN)),
            "estimated_completion_tokens": max(0, int(settings.LLM_ESTIMATED_COMPLETION_TOKENS)),
        }

    def enforce_chat_request(self, *, messages: list[dict], user_id: int | None, action: str) -> dict[str, Any]:
        estimated_input_tokens = 0
        for message in messages or []:
            if not isinstance(message, dict):
                continue
            estimated_input_tokens += 6
            estimated_input_tokens += self._estimate_text_tokens(str(message.get("content") or ""))
        policy = get_task_policy(action)
        return self._enforce(
            user_id=user_id,
            action=action,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=self.get_policy()["estimated_completion_tokens"],
            budget_category=policy.budget_category,
            rate_limit_category=policy.rate_limit_category,
        )

    def enforce_generate_request(self, *, prompt: str, user_id: int | None, action: str) -> dict[str, Any]:
        policy = get_task_policy(action)
        return self._enforce(
            user_id=user_id,
            action=action,
            estimated_input_tokens=self._estimate_text_tokens(prompt),
            estimated_output_tokens=self.get_policy()["estimated_completion_tokens"],
            budget_category=policy.budget_category,
            rate_limit_category=policy.rate_limit_category,
        )

    def enforce_embedding_request(self, *, texts: list[str], user_id: int | None, action: str = "embedding") -> dict[str, Any]:
        total_chars = sum(len(text or "") for text in texts if isinstance(text, str))
        estimated_input_tokens = self._estimate_text_tokens_by_chars(total_chars)
        policy = get_task_policy(action)
        return self._enforce(
            user_id=user_id,
            action=action,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=0,
            budget_category=policy.budget_category,
            rate_limit_category=policy.rate_limit_category,
        )

    def get_user_status(self, db: Session, user_id: int, budget_category: str | None = None, rate_limit_category: str | None = None) -> dict[str, Any]:
        policy = self.get_policy()
        today_start = self._today_start()
        usage_query = db.query(
            func.count(TokenUsage.id),
            func.coalesce(func.sum(TokenUsage.total_tokens), 0),
        ).filter(
            TokenUsage.user_id == user_id,
            TokenUsage.created_at >= today_start,
        )
        if budget_category:
            usage_query = usage_query.filter(TokenUsage.budget_category == budget_category)
        usage_row = usage_query.first()
        blocked_count = (
            db.query(func.count(LLMCallLog.id))
            .filter(
                LLMCallLog.user_id == user_id,
                LLMCallLog.status == "blocked",
                LLMCallLog.created_at >= today_start,
            )
            .scalar()
            or 0
        )
        used_requests = int(usage_row[0] or 0)
        used_tokens = int(usage_row[1] or 0)
        rate_window = self._rate_config(rate_limit_category, "window_seconds", policy["rate_limit_window_seconds"])
        return {
            "today": {
                "used_requests": used_requests,
                "used_tokens": used_tokens,
                "blocked_requests": int(blocked_count),
                "remaining_requests": self._remaining(policy["daily_request_limit"], used_requests),
                "remaining_tokens": self._remaining(policy["daily_token_limit"], used_tokens),
            },
            "rate_limit": {
                "window_seconds": rate_window,
                "max_requests": self._rate_config(rate_limit_category, "max_requests", policy["rate_limit_max_requests"]),
                "current_requests": self._get_rate_counter_value(
                    user_id=user_id,
                    window_seconds=rate_window,
                    rate_limit_category=rate_limit_category,
                ),
            },
            "policy": policy,
        }

    def get_global_status(self, db: Session) -> dict[str, Any]:
        policy = self.get_policy()
        today_start = self._today_start()
        usage_row = (
            db.query(
                func.count(TokenUsage.id),
                func.coalesce(func.sum(TokenUsage.total_tokens), 0),
            )
            .filter(TokenUsage.created_at >= today_start)
            .first()
        )
        blocked_count = (
            db.query(func.count(LLMCallLog.id))
            .filter(
                LLMCallLog.status == "blocked",
                LLMCallLog.created_at >= today_start,
            )
            .scalar()
            or 0
        )
        return {
            "today": {
                "total_requests": int(usage_row[0] or 0),
                "total_tokens": int(usage_row[1] or 0),
                "blocked_requests": int(blocked_count),
            },
            "policy": policy,
        }

    def reset_local_state(self) -> None:
        global _fallback_rate_counters
        with _fallback_rate_lock:
            _fallback_rate_counters = {}
        if self._redis_client is not None:
            try:
                keys = self._redis_client.keys(f"{settings.LLM_LIMIT_REDIS_PREFIX}:rate:*")
                if keys:
                    self._redis_client.delete(*keys)
            except Exception:
                pass
        self._redis_client = None
        self._redis_unavailable = False

    def _enforce(
        self,
        *,
        user_id: int | None,
        action: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        budget_category: str,
        rate_limit_category: str,
    ) -> dict[str, Any]:
        policy = self.get_policy()
        daily_request_limit = self._budget_limit(budget_category, "daily_requests", policy["daily_request_limit"])
        daily_token_limit = self._budget_limit(budget_category, "daily_tokens", policy["daily_token_limit"])
        rate_window = self._rate_config(rate_limit_category, "window_seconds", policy["rate_limit_window_seconds"])
        rate_max = self._rate_config(rate_limit_category, "max_requests", policy["rate_limit_max_requests"])
        estimated_total_tokens = estimated_input_tokens + estimated_output_tokens
        if user_id is None:
            return {
                "estimated_input_tokens": estimated_input_tokens,
                "estimated_output_tokens": estimated_output_tokens,
                "estimated_total_tokens": estimated_total_tokens,
                "budget_category": budget_category,
                "rate_limit_category": rate_limit_category,
                "policy": policy,
            }

        db = SessionLocal()
        try:
            status = self.get_user_status(db, user_id, budget_category=budget_category, rate_limit_category=rate_limit_category)
        finally:
            db.close()

        today = status["today"]
        if daily_request_limit and today["used_requests"] >= daily_request_limit:
            detail = {
                "action": action,
                "budget_category": budget_category,
                "limit": daily_request_limit,
                "used": today["used_requests"],
                "remaining": 0,
            }
            self._log_blocked_request(user_id=user_id, action=action, code="LLM_DAILY_REQUEST_BUDGET_EXCEEDED", message="今日模型调用次数已达上限", detail=detail)
            raise LLMGovernanceError(
                status_code=429,
                code="LLM_DAILY_REQUEST_BUDGET_EXCEEDED",
                message="今日模型调用次数已达上限",
                detail=detail,
            )

        if daily_token_limit and today["used_tokens"] + estimated_total_tokens > daily_token_limit:
            detail = {
                "action": action,
                "budget_category": budget_category,
                "limit": daily_token_limit,
                "used": today["used_tokens"],
                "estimated_request_tokens": estimated_total_tokens,
                "remaining": max(daily_token_limit - today["used_tokens"], 0),
            }
            self._log_blocked_request(user_id=user_id, action=action, code="LLM_DAILY_TOKEN_BUDGET_EXCEEDED", message="今日 Token 预算已用尽", detail=detail)
            raise LLMGovernanceError(
                status_code=429,
                code="LLM_DAILY_TOKEN_BUDGET_EXCEEDED",
                message="今日 Token 预算已用尽",
                detail=detail,
            )

        if rate_window and rate_max:
            current_requests = self._increment_rate_counter(
                user_id=user_id,
                window_seconds=rate_window,
                rate_limit_category=rate_limit_category,
            )
            if current_requests > rate_max:
                detail = {
                    "action": action,
                    "rate_limit_category": rate_limit_category,
                    "window_seconds": rate_window,
                    "limit": rate_max,
                    "current": current_requests,
                }
                self._log_blocked_request(user_id=user_id, action=action, code="LLM_RATE_LIMIT_EXCEEDED", message="请求过于频繁，请稍后再试", detail=detail)
                raise LLMGovernanceError(
                    status_code=429,
                    code="LLM_RATE_LIMIT_EXCEEDED",
                    message="请求过于频繁，请稍后再试",
                    detail=detail,
                )

        return {
            "estimated_input_tokens": estimated_input_tokens,
            "estimated_output_tokens": estimated_output_tokens,
            "estimated_total_tokens": estimated_total_tokens,
            "budget_category": budget_category,
            "rate_limit_category": rate_limit_category,
            "policy": policy,
            "today": today,
        }

    def _estimate_text_tokens(self, text: str | None) -> int:
        return self._estimate_text_tokens_by_chars(len(text or ""))

    def _estimate_text_tokens_by_chars(self, total_chars: int) -> int:
        if total_chars <= 0:
            return 0
        return max(1, math.ceil(total_chars / max(1, int(settings.LLM_ESTIMATED_CHARS_PER_TOKEN))))

    @staticmethod
    def _today_start() -> datetime:
        now = utc_now()
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _remaining(limit: int, used: int) -> int | None:
        if limit <= 0:
            return None
        return max(limit - used, 0)

    @staticmethod
    def _parse_category_json(raw: str) -> dict[str, dict[str, Any]]:
        try:
            cfg = json.loads(raw)
        except Exception:
            return {}
        return cfg if isinstance(cfg, dict) else {}

    def _budget_limit(self, category: str, key: str, default: int) -> int:
        cfg = self._parse_category_json(settings.LLM_BUDGET_LIMITS_JSON)
        category_cfg = cfg.get(category, {}) if isinstance(cfg, dict) else {}
        return int(category_cfg.get(key, default))

    def _rate_config(self, category: str | None, key: str, default: int) -> int:
        cfg = self._parse_category_json(settings.LLM_RATE_LIMIT_CONFIG_JSON)
        category_cfg = cfg.get(category, {}) if isinstance(cfg, dict) and category else {}
        return int(category_cfg.get(key, default))

    def _redis(self) -> redis.Redis | None:
        if self._redis_unavailable:
            return None
        if self._redis_client is None:
            try:
                self._redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
                self._redis_client.ping()
            except Exception:
                self._redis_unavailable = True
                self._redis_client = None
        return self._redis_client

    def _rate_key(self, *, user_id: int, window_seconds: int, rate_limit_category: str | None) -> str:
        bucket = int(time.time() // max(window_seconds, 1))
        category = rate_limit_category or "chat"
        return f"{settings.LLM_LIMIT_REDIS_PREFIX}:rate:{category}:{user_id}:{window_seconds}:{bucket}"

    def _increment_rate_counter(self, *, user_id: int, window_seconds: int, rate_limit_category: str | None) -> int:
        if window_seconds <= 0:
            return 0
        key = self._rate_key(user_id=user_id, window_seconds=window_seconds, rate_limit_category=rate_limit_category)
        client = self._redis()
        if client is not None:
            try:
                count = int(client.incr(key))
                if count == 1:
                    client.expire(key, window_seconds + 5)
                return count
            except Exception:
                self._redis_unavailable = True
                self._redis_client = None
        return self._increment_fallback_counter(key=key, window_seconds=window_seconds)

    def _get_rate_counter_value(self, *, user_id: int, window_seconds: int, rate_limit_category: str | None) -> int:
        if window_seconds <= 0:
            return 0
        key = self._rate_key(user_id=user_id, window_seconds=window_seconds, rate_limit_category=rate_limit_category)
        client = self._redis()
        if client is not None:
            try:
                value = client.get(key)
                return int(value or 0)
            except Exception:
                self._redis_unavailable = True
                self._redis_client = None
        return self._get_fallback_counter(key=key)

    def _increment_fallback_counter(self, *, key: str, window_seconds: int) -> int:
        now = time.time()
        with _fallback_rate_lock:
            self._cleanup_fallback_counters(now)
            current, expires_at = _fallback_rate_counters.get(key, (0, now + window_seconds + 5))
            if expires_at <= now:
                current = 0
                expires_at = now + window_seconds + 5
            current += 1
            _fallback_rate_counters[key] = (current, expires_at)
            return current

    def _get_fallback_counter(self, *, key: str) -> int:
        now = time.time()
        with _fallback_rate_lock:
            self._cleanup_fallback_counters(now)
            current, expires_at = _fallback_rate_counters.get(key, (0, now))
            if expires_at <= now:
                return 0
            return current

    @staticmethod
    def _cleanup_fallback_counters(now: float) -> None:
        expired_keys = [key for key, (_, expires_at) in _fallback_rate_counters.items() if expires_at <= now]
        for key in expired_keys:
            _fallback_rate_counters.pop(key, None)

    def _log_blocked_request(
        self,
        *,
        user_id: int,
        action: str,
        code: str,
        message: str,
        detail: dict[str, Any],
    ) -> None:
        llm_observability_service.log_event(
            module_name=self._infer_module_name(action),
            action=action,
            model_name=settings.LLM_MODEL,
            status="blocked",
            user_id=user_id,
            error_message=f"{code}: {message}",
            request_excerpt={"action": action, "governance_code": code},
            response_excerpt=detail,
        )

    @staticmethod
    def _infer_module_name(action: str) -> str:
        if action == "embedding":
            return "document"
        if action.startswith("document_") or action.startswith("rag_"):
            return "document"
        if action.startswith("meeting_"):
            return "meeting"
        if action.startswith("email_"):
            return "email"
        if action.startswith("task_"):
            return "task"
        if action.startswith("agent_"):
            return "agent"
        if action.startswith("chat"):
            return "chat"
        return "system"


llm_governance_service = LLMGovernanceService()
