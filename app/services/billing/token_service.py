import json
from datetime import timedelta
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models.token_usage import TokenUsage
from app.core.config import get_settings
from app.core.time import utc_now

settings = get_settings()


class TokenService:
    def compute_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
        """按 LLM_MODEL_PRICING 计算本 attempt 成本；未配置定价的模型按 0 计。

        全程使用 Decimal 计算并 quantize 到 6 位小数，避免 float 承载金额
        在汇总/对账中的精度漂移（成本台账金额统一 Decimal）。
        """
        try:
            pricing = json.loads(settings.LLM_MODEL_PRICING)
            model_pricing = pricing.get(model, {}) if isinstance(pricing, dict) else {}
            input_per_1k = Decimal(str(model_pricing.get("input_per_1k") or 0))
            output_per_1k = Decimal(str(model_pricing.get("output_per_1k") or 0))
        except Exception:
            return Decimal("0")
        cost = (
            Decimal(prompt_tokens) / Decimal(1000) * input_per_1k
            + Decimal(completion_tokens) / Decimal(1000) * output_per_1k
        )
        return cost.quantize(Decimal("0.000001"))

    def record(
        self,
        model: str,
        db: Session,
        user_id: int | None = None,
        action: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        duration_ms: int | None = None,
        budget_category: str | None = None,
        attempt_number: int | None = 1,
        cost: float | Decimal | None = None,
    ) -> TokenUsage:
        total = prompt_tokens + completion_tokens
        if cost is None:
            cost = self.compute_cost(model, prompt_tokens, completion_tokens)
        usage = TokenUsage(
            user_id=user_id,
            model=model,
            action=action,
            budget_category=budget_category,
            attempt_number=attempt_number,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            cost=cost,
            duration_ms=duration_ms,
        )
        db.add(usage)
        db.flush()  # 取得 id 供成本台账幂等入账
        # 统一成本台账（Decimal 精度；同事务，来源去重）
        if user_id:
            from app.services.billing.cost_ledger_service import cost_ledger_service
            cost_ledger_service.record_llm_cost(
                db=db, user_id=user_id, model=model, action=action or "",
                cost=Decimal(str(cost)), prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, token_usage_id=usage.id,
            )
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
        total_cost = round(sum(r.cost or 0 for r in rows), 6)
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
            "total_cost": total_cost,
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
        total_cost = round(sum(r.cost or 0 for r in rows), 6)

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
            "total_cost": total_cost,
            "by_model": by_model,
            "by_date": by_date,
        }


token_service = TokenService()
