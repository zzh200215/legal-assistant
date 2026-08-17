"""WebSocket 会话事件日志：断线恢复的持久化事件来源。

- 仅状态事件（job_update / notification / run_snapshot 等）落库，供 resume 补发；
  流式 chunk 标记 volatile=1 不落库（恢复语义 = 重新发起 run）。
- resume_token 为能力令牌，绑定 user_id / organization_id / channel / expires_at；
  凭任意 sequence 无法读取他人事件（查询强制 user_id + token 匹配）。
- UNIQUE(session_id, seq_no)：会话内序号单调递增，补发按 seq > ack_seq 过滤。
"""

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint, func

from app.core.database import Base


class WsEventLog(Base):
    __tablename__ = "ws_event_logs"
    __table_args__ = (
        UniqueConstraint("session_id", "seq_no", name="uq_ws_event_logs_session_seq"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, index=True, comment="会话 UUID（hex）")
    resume_token = Column(String(64), nullable=True, index=True,
                          comment="恢复令牌（能力令牌，绑定下方 user/org/channel；会话内多行共享同一令牌）")
    user_id = Column(Integer, nullable=False, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    channel = Column(String(32), nullable=True, comment="订阅通道：chat / agent / jobs / notifications")
    seq_no = Column(Integer, nullable=False, comment="会话内单调递增序号")
    event_type = Column(String(32), nullable=False, comment="事件类型：welcome/chunk/done/job_update/...")
    payload_json = Column(Text, nullable=True, comment="事件负载（经 redact_payload 脱敏）")
    volatile = Column(Integer, nullable=False, default=0, server_default="0",
                      comment="1=可丢弃流式事件（不参与持久恢复）")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True,
                        comment="事件与 resume token 过期时间")
