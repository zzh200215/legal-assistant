from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class LLMCallLog(Base):
    __tablename__ = "llm_call_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    module_name = Column(String(64), nullable=False, index=True)
    action = Column(String(128), nullable=False, index=True)
    model_name = Column(String(128), nullable=False)
    prompt_template = Column(String(128), nullable=True, index=True)
    prompt_version = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default="success", index=True)
    request_id = Column(String(36), nullable=True, index=True)
    routing_role = Column(String(16), nullable=True, index=True)
    routing_stage = Column(String(16), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    request_excerpt = Column(Text, nullable=True)
    response_excerpt = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
