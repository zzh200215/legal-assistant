from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.core.database import Base


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True, index=True)
    title = Column(String(256), nullable=False)
    transcript = Column(Text, nullable=True)
    transcript_segments = Column(Text, nullable=True)
    transcript_source = Column(String(32), nullable=True)
    audio_path = Column(String(512), nullable=True)
    status = Column(String(32), default="pending", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    summary = relationship("MeetingSummary", back_populates="meeting", uselist=False, cascade="all, delete-orphan")


class MeetingSummary(Base):
    __tablename__ = "meeting_summaries"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    meeting_id = Column(Integer, ForeignKey("meetings.id"), nullable=False, unique=True, index=True)
    theme = Column(String(256), nullable=True)
    summary = Column(Text, nullable=True)
    topics = Column(Text, nullable=True)
    decisions = Column(Text, nullable=True)
    action_items = Column(Text, nullable=True)
    risks = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    meeting = relationship("Meeting", back_populates="summary")
