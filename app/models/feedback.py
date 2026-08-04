"""#72/退出问卷与 NPS 回收模型（试点退出调查，对应 pilot-success-playbook §5）"""
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, func
from app.core.database import Base


class ExitSurvey(Base):
    __tablename__ = "exit_surveys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    nps_score = Column(Integer, nullable=True)
    trust_confidence = Column(String(16), nullable=True)
    trust_citations = Column(String(16), nullable=True)
    trust_next_steps = Column(String(16), nullable=True)
    value_ranking = Column(String(64), nullable=True)
    review_wish = Column(Text, nullable=True)
    pain_point = Column(Text, nullable=True)
    pay_intent = Column(String(16), nullable=True)
    feature_requests = Column(Text, nullable=True)
    summary_feedback = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class NpsResponse(Base):
    __tablename__ = "nps_responses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    score = Column(Integer, nullable=False)
    source = Column(String(16), nullable=False, server_default="in_app")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
