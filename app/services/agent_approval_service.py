from __future__ import annotations

import json
import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.agent import AgentApprovalRequest
from app.core.time import utc_now


class AgentApprovalService:
    HIGH_RISK_TOOLS = {
        "task_create_tool": "high",
        "meeting_action_tool": "high",
        # Even read-only SQL can expose organization-wide sensitive data.
        "sql_query_tool": "high",
    }

    def requires_approval(self, tool_name: str) -> bool:
        return tool_name in self.HIGH_RISK_TOOLS

    def create_request(
        self,
        *,
        db: Session,
        user_id: int,
        tool_name: str,
        input_params: dict,
        agent_type: str,
        agent_run_id: int | None = None,
    ) -> AgentApprovalRequest:
        request = AgentApprovalRequest(
            agent_run_id=agent_run_id,
            user_id=user_id,
            tool_name=tool_name,
            agent_type=agent_type,
            input_params=json.dumps(input_params, ensure_ascii=False),
            risk_level=self.HIGH_RISK_TOOLS.get(tool_name, "high"),
            status="pending",
            approval_token=secrets.token_hex(16),
        )
        db.add(request)
        db.commit()
        db.refresh(request)
        return request

    def list_requests(self, *, db: Session, user_id: int, status: str | None = None) -> list[AgentApprovalRequest]:
        query = db.query(AgentApprovalRequest).filter(AgentApprovalRequest.user_id == user_id)
        if status:
            query = query.filter(AgentApprovalRequest.status == status)
        return query.order_by(AgentApprovalRequest.created_at.desc(), AgentApprovalRequest.id.desc()).all()

    def get_request(self, *, db: Session, approval_id: int, user_id: int) -> AgentApprovalRequest | None:
        return (
            db.query(AgentApprovalRequest)
            .filter(AgentApprovalRequest.id == approval_id, AgentApprovalRequest.user_id == user_id)
            .first()
        )

    def decide_request(
        self,
        *,
        db: Session,
        approval_id: int,
        user_id: int,
        approved: bool,
        decision_note: str | None = None,
    ) -> AgentApprovalRequest:
        request = self.get_request(db=db, approval_id=approval_id, user_id=user_id)
        if not request:
            raise ValueError("Approval request not found")
        if request.status != "pending":
            raise ValueError("Approval request already decided")
        request.status = "approved" if approved else "rejected"
        request.decision_note = decision_note
        request.decided_at = utc_now()
        db.add(request)
        db.commit()
        db.refresh(request)
        return request

    def mark_executed(
        self,
        *,
        db: Session,
        approval_id: int,
        user_id: int,
        decision_note: str | None = None,
    ) -> AgentApprovalRequest:
        request = self.get_request(db=db, approval_id=approval_id, user_id=user_id)
        if not request:
            raise ValueError("Approval request not found")
        request.status = "executed"
        if decision_note:
            request.decision_note = decision_note
        request.decided_at = request.decided_at or utc_now()
        db.add(request)
        db.commit()
        db.refresh(request)
        return request


agent_approval_service = AgentApprovalService()
