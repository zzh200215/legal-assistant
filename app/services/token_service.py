import time
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.token_usage import TokenUsage
from app.core.time import utc_now


class TokenService:
    def record(
        self,
        model: str,
        db: Session,
        user_id: int | None = None,
        action: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: int | None = None,
    ) -> TokenUsage:
        total = prompt_tokens + completion_tokens
        usage = TokenUsage(
            user_id=user_id,
            model=model,
            action=action,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            duration_ms=duration_ms,
        )
        db.add(usage)
        db.commit()
        db.refresh(usage)
        return usage

    def get_user_stats(self, user_id: int, db: Session, days: int = 30) -> dict:
        """获取用户在指定天数内的统计"""
        since = utc_now() - timedelta(days=days)
        rows = db.query(TokenUsage).filter(
            TokenUsage.user_id == user_id,
            TokenUsage.created_at >= since,
        ).all()

        total_calls = len(rows)
        total_prompt = sum(r.prompt_tokens for r in rows)
        total_completion = sum(r.completion_tokens for r in rows)
        total_tokens = sum(r.total_tokens for r in rows)
        total_duration = sum(r.duration_ms or 0 for r in rows)

        # 按 action 分组
        by_action = {}
        for r in rows:
            key = r.action or "unknown"
            if key not in by_action:
                by_action[key] = {"calls": 0, "total_tokens": 0}
            by_action[key]["calls"] += 1
            by_action[key]["total_tokens"] += r.total_tokens

        # 按日期分组
        by_date = {}
        for r in rows:
            key = r.created_at.strftime("%Y-%m-%d") if r.created_at else "unknown"
            if key not in by_date:
                by_date[key] = {"calls": 0, "total_tokens": 0}
            by_date[key]["calls"] += 1
            by_date[key]["total_tokens"] += r.total_tokens

        return {
            "days": days,
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "avg_duration_ms": round(total_duration / total_calls) if total_calls else 0,
            "by_action": by_action,
            "by_date": by_date,
        }

    def get_global_stats(self, db: Session, days: int = 30) -> dict:
        """获取全局统计"""
        since = utc_now() - timedelta(days=days)
        rows = db.query(TokenUsage).filter(TokenUsage.created_at >= since).all()

        total_calls = len(rows)
        total_tokens = sum(r.total_tokens for r in rows)
        total_prompt = sum(r.prompt_tokens for r in rows)
        total_completion = sum(r.completion_tokens for r in rows)

        # 按 model 分组
        by_model = {}
        for r in rows:
            key = r.model
            if key not in by_model:
                by_model[key] = {"calls": 0, "total_tokens": 0}
            by_model[key]["calls"] += 1
            by_model[key]["total_tokens"] += r.total_tokens

        # 按日期分组
        by_date = {}
        for r in rows:
            key = r.created_at.strftime("%Y-%m-%d") if r.created_at else "unknown"
            if key not in by_date:
                by_date[key] = {"calls": 0, "total_tokens": 0}
            by_date[key]["calls"] += 1
            by_date[key]["total_tokens"] += r.total_tokens

        return {
            "days": days,
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "by_model": by_model,
            "by_date": by_date,
        }


token_service = TokenService()
