from sqlalchemy import Column, Integer, String, Text, DateTime, Float, ForeignKey, func
from sqlalchemy.orm import relationship
from app.core.database import Base


class TokenUsage(Base):
    __tablename__ = "token_usage"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    model = Column(String(128), nullable=False)
    action = Column(String(128), nullable=True)
    # 独立预算桶（text/embedding/vision/rerank），与 TaskPolicy.budget_category 一致。
    budget_category = Column(String(32), nullable=True, index=True)
    # 逻辑请求内的第几次 attempt（1 起）；重试/fallback 行与最终成功行据此区分。
    attempt_number = Column(Integer, nullable=True, default=1)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    # 本 attempt 成本（按 LLM_MODEL_PRICING 与 LLM_PRICE_CURRENCY 计算）。
    cost = Column(Float, nullable=True, default=0.0)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
