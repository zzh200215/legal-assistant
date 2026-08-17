"""PermissionGuard：统一的权限校验边界（不执行任何工具）。

覆盖三层：
- ``tool_acl``：agent 角色 → 工具 ACL（复用 app.mcp.permissions）。
- ``authz_snapshot``：Agent 长流程权限快照（复用 authorization_service.assert_snapshot，
  账号禁用/组织撤销/文档授权撤销立即终止）。
- ``plan``：执行计划级校验（风险等级/写审批前置）。

所有拒绝都返回结构化 ``PermissionDecision``（可审计），绝不暴露敏感资源信息。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.mcp.permissions import agent_allows_tool, allowed_tools_for, canonical_agent_type
from app.models.agent import AgentRun

DECISION_KIND_ACL = "tool_acl"
DECISION_KIND_SNAPSHOT = "authz_snapshot"
DECISION_KIND_PLAN = "plan"


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    reason: str | None = None
    error_code: str | None = None
    decision_kind: str = DECISION_KIND_ACL
    agent_type: str | None = None
    tool_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "error_code": self.error_code,
            "decision_kind": self.decision_kind,
            "agent_type": self.agent_type,
            "tool_name": self.tool_name,
        }


class PermissionGuard:
    def check_tool_acl(self, agent_type: str, tool_name: str) -> PermissionDecision:
        """角色 × 工具 ACL。工具不在该角色允许集内 → 拒绝。"""
        canonical = canonical_agent_type(agent_type)
        if agent_allows_tool(canonical, tool_name):
            return PermissionDecision(
                allowed=True, agent_type=canonical, tool_name=tool_name, decision_kind=DECISION_KIND_ACL
            )
        return PermissionDecision(
            allowed=False,
            reason="Agent 角色无权调用该工具",
            error_code="MCP_PERMISSION_DENIED",
            agent_type=canonical,
            tool_name=tool_name,
            decision_kind=DECISION_KIND_ACL,
        )

    def check_run_snapshot(self, db: Session, *, agent_run_id: int, user_id: int) -> PermissionDecision:
        """长流程权限快照：无快照视为通过；快照失效（禁用/撤销/过期/token 失效）→ 拒绝。"""
        from app.services.org.authorization_service import authorization_service

        run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).first()
        snapshot_id = run.authorization_snapshot_id if run else None
        if not snapshot_id:
            return PermissionDecision(allowed=True, decision_kind=DECISION_KIND_SNAPSHOT)
        try:
            authorization_service.assert_snapshot(db, snapshot_id, user_id=user_id)
            return PermissionDecision(allowed=True, decision_kind=DECISION_KIND_SNAPSHOT)
        except Exception as exc:  # noqa: BLE001 - 统一映射为拒绝，不泄露细节
            code = getattr(getattr(exc, "detail", None), "get", lambda *_: "authz_changed")(
                "code", "authz_changed"
            )
            return PermissionDecision(
                allowed=False,
                reason="执行已终止：权限已变化，请重新发起",
                error_code=code,
                decision_kind=DECISION_KIND_SNAPSHOT,
            )

    def check_tool_execution(
        self,
        *,
        agent_type: str,
        tool_name: str,
        db: Session | None,
        agent_run_id: int | None,
        user_id: int | None,
    ) -> PermissionDecision:
        """工具执行前统一校验：先 ACL，后权限快照。任一拒绝即拒绝。"""
        acl = self.check_tool_acl(agent_type, tool_name)
        if not acl.allowed:
            return acl
        if db is not None and agent_run_id is not None and user_id is not None:
            snapshot = self.check_run_snapshot(db, agent_run_id=agent_run_id, user_id=user_id)
            if not snapshot.allowed:
                return snapshot
        return acl

    def check_plan(self, plan: dict[str, Any] | None) -> PermissionDecision:
        """计划级校验：写审批前置。计划要求审批但上下文不允许 → 拒绝执行。"""
        if not isinstance(plan, dict):
            return PermissionDecision(allowed=True, decision_kind=DECISION_KIND_PLAN)
        if plan.get("requires_approval") and plan.get("approval_context_missing"):
            return PermissionDecision(
                allowed=False,
                reason="计划包含需要审批的步骤但缺少审批上下文",
                error_code="PLAN_APPROVAL_REQUIRED",
                decision_kind=DECISION_KIND_PLAN,
            )
        return PermissionDecision(allowed=True, decision_kind=DECISION_KIND_PLAN)

    def denied_result(self, decision: PermissionDecision) -> dict[str, Any]:
        """把拒绝决策映射为与 MCP registry 一致的错误响应结构。

        权限快照失效统一用 AUTHZ_CHANGED（与历史 _assert_run_snapshot 契约一致），
        具体错误码保留在 data.error_code。
        """
        if decision.decision_kind == DECISION_KIND_SNAPSHOT:
            mcp_code = "AUTHZ_CHANGED"
        else:
            mcp_code = decision.error_code or "MCP_PERMISSION_DENIED"
        return {
            "success": False,
            "message": decision.reason or "权限拒绝",
            "data": {
                "agent_type": decision.agent_type,
                "requested_tool": decision.tool_name,
                "decision_kind": decision.decision_kind,
                "error_code": decision.error_code,
            },
            "error": decision.reason or "权限拒绝",
            "mcp_error_code": mcp_code,
            "mcp_http_status": 403,
        }


permission_guard = PermissionGuard()
