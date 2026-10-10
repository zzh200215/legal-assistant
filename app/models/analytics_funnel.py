"""产品漏斗埋点（ux-audit 需补充信息：注册→首个任务完成漏斗无法量化）。

最小事件集：auth_register / auth_login / case_create / consult_submit / review_submit。
只记行为元数据（谁、何时、哪个案件），不记业务内容文本；门户访问复用
legal_portal_access_logs，不在此重复记录。
"""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func

from app.core.database import Base

FUNNEL_EVENTS = ("auth_register", "auth_login", "case_create", "consult_submit", "review_submit")


class AnalyticsFunnelEvent(Base):
    __tablename__ = "analytics_funnel_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    event = Column(String(64), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    case_id = Column(Integer, nullable=True)
    path = Column(String(256), nullable=True)
    props_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
