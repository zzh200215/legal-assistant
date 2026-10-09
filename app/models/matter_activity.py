"""Append-only, non-sensitive activity entries for legal matters."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base


class MatterActivity(Base):
    __tablename__ = "matter_activities"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    case_id = Column(Integer, ForeignKey("legal_cases.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    target_type = Column(String(32), nullable=True)
    target_id = Column(Integer, nullable=True)
    title = Column(String(256), nullable=False)
    summary = Column(String(1000), nullable=True)
    visibility = Column(String(16), nullable=False, default="internal", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
