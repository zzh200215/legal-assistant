"""端到端落地验证（一次性）：全新临时库 → 应用启动 → 真实 agent 链路。

验证点：
1. 完整 schema（含 0073 新增列/表）在全新 sqlite 库可创建、应用可启动。
2. HTTP 层：鉴权、/api/agent/registry、/api/agent/harness、/api/agent/preview 可用。
3. Agent 全链路：读工具执行 → 写工具触发审批(带 param_digest/expires_at) →
   审批后恢复执行 → run 状态转移 → 结构化审计事件落库 → 任务真实创建。
4. 幂等/审计表可写。

注意：alembic 全链在 sqlite 从零被既有 0004 外键 ALTER 阻塞（P0 已记录，不影响 MySQL）；
此处用 Base.metadata.create_all 得到与模型一致的完整 schema 做链路验证。
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

# 把 repo 根加入 sys.path（脚本运行于 scripts/ 子目录时）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 必须在导入 app 前把 DATABASE_URL 指向临时库
_TMP_DIR = tempfile.mkdtemp(prefix="aibg_e2e_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP_DIR, 'e2e.db')}"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.auth import create_access_token  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, SessionLocal, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.mcp.tool_contract import ToolContract  # noqa: E402
from app.models.agent import AgentAuditEvent, AgentApprovalRequest, AgentRun  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.agent.agent_approval_service import agent_approval_service  # noqa: E402
from app.services.agent.agent_service import agent_service  # noqa: E402
from app.tools.base import BaseAgentTool, tool_success  # noqa: E402


def _make_fake_tool(tool_name, **contract_kwargs):
    class FakeTool(BaseAgentTool):
        name = tool_name
        contract = ToolContract(name=tool_name, **contract_kwargs)
        parameters = {"type": "object", "properties": {}, "required": []}

        async def run(self, **kwargs):
            if tool_name == "task_create_tool":
                task = Task(user_id=kwargs["user_id"], title=kwargs["title"], status="todo")
                _DB.add(task)
                _DB.commit()
                _DB.refresh(task)
                return tool_success("任务已创建", {"task": {"id": task.id, "title": kwargs["title"], "status": "todo"}})
            return tool_success("ok", {"echo": kwargs})

    return FakeTool()


def main() -> None:
    global _DB
    Base.metadata.create_all(bind=SessionLocal().get_bind())
    db = SessionLocal()
    _DB = db

    # ── 用户与鉴权 ──────────────────────────────────────────────
    user = User(username="e2e", email="e2e@test.com", hashed_password="h", role="admin")
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token({"sub": str(user.id)})
    headers = {"Authorization": f"Bearer {token}"}
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)

    # ── 1. HTTP 层：应用启动 + 基础端点可用（新 schema 下）──────
    for method, path in (("GET", "/api/agent/registry"), ("GET", "/api/agent/harness")):
        resp = client.get(path, headers=headers)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text[:200]}"
        print(f"[OK] {method} {path} -> {resp.status_code}")
    resp = client.post("/api/agent/preview", json={"goal": "总结文档 5，并提取其中的风险点"}, headers=headers)
    assert resp.status_code == 200, f"preview -> {resp.status_code}: {resp.text[:200]}"
    preview_data = resp.json().get("data") if isinstance(resp.json(), dict) else resp.json()
    print(f"[OK] POST /api/agent/preview -> {resp.status_code} can_execute={preview_data.get('can_execute')}")

    # ── 2. Agent 全链路（读 → 写 → 审批 → 恢复 → 审计）─────────
    fake_tools = {
        "document_summary_tool": _make_fake_tool("document_summary_tool", read_only=True, requires_approval=False),
        "document_risk_tool": _make_fake_tool("document_risk_tool", read_only=True, requires_approval=False),
        "task_create_tool": _make_fake_tool("task_create_tool", read_only=False, requires_approval=True, idempotency_keyed=True),
    }
    decisions = [
        '{"thought":"总结文档","action_type":"tool_call","tool_name":"document_summary_tool","action_input":{"document_id":1}}',
        '{"thought":"提取风险","action_type":"tool_call","tool_name":"document_risk_tool","action_input":{"document_id":1}}',
        '{"thought":"知识完成","action_type":"finish","answer":"文档总结与风险已提取。"}',
        '{"thought":"创建任务","action_type":"tool_call","tool_name":"task_create_tool","action_input":{"title":"跟进任务"}}',
        '{"thought":"完成","action_type":"finish","answer":"任务已创建。"}',
    ]

    async def fake_chat(messages, stream=False, temperature=0.7, **kw):
        return decisions.pop(0)

    async def fake_generate(prompt, temperature=0.1, **kw):
        return json.dumps({
            "intent": "审查后创建任务", "workers": ["knowledge_agent", "workflow_agent"],
            "dependencies": [{"from": "knowledge_agent", "to": "workflow_agent"}],
            "risk_level": "medium", "expected_artifacts": ["document", "task"],
        })

    run = None
    with patch.dict("app.mcp.registry._TOOL_INSTANCES", fake_tools, clear=True), \
         patch("app.services.agent.agent_service.llm_service.chat", side_effect=fake_chat), \
         patch("app.services.agent.agent_service.llm_service.generate", side_effect=fake_generate):
        run = asyncio.run(agent_service.run("总结文档 1 并创建跟进任务", user.id, db, max_steps=6))
        print(f"[OK] run -> {run.status} (awaiting_approval)")

    assert run.status == "awaiting_approval"
    approval = agent_approval_service.list_requests(db=db, user_id=user.id, status="pending")[0]
    assert approval.tool_name == "task_create_tool"
    assert approval.param_digest and approval.expires_at, "审批必须绑定参数摘要与过期时间"
    print(f"[OK] 审批创建: tool={approval.tool_name} digest={approval.param_digest[:12]}… expires_at={approval.expires_at}")

    # 恢复执行（审批通过）
    agent_approval_service.decide_request(db=db, approval_id=approval.id, user_id=user.id, approved=True, decision_note="ok")
    resumed = asyncio.run(agent_service.resume_after_approval(approval.id, user.id, db))
    print(f"[OK] resume -> {resumed.status}")

    assert resumed.status == "completed", f"run should complete, got {resumed.status}"
    # 任务真实创建
    task = db.query(Task).filter(Task.user_id == user.id).first()
    assert task and task.status == "todo"
    print(f"[OK] 任务真实创建: id={task.id} status={task.status}")
    assert agent_approval_service.get_request(db=db, approval_id=approval.id, user_id=user.id).status == "executed"

    # 审计事件落库（plan/tool/approval/step）
    events = db.query(AgentAuditEvent).filter(AgentAuditEvent.run_id == resumed.id).all()
    types = {e.event_type for e in events}
    print(f"[OK] 审计事件 {len(events)} 条: {sorted(types)}")
    assert "plan_created" in types and "tool_executed" in types and "approval_created" in types

    # 具类型 state 持久化在 workflow_state["model"]
    raw_snapshot = json.loads(resumed.workflow_state)
    assert "model" in raw_snapshot and raw_snapshot["model"].get("plan")
    print("[OK] 具类型 AgentRunState 持久化于 workflow_state.model")

    # ── 3. HTTP 层：新 schema 下查询 run 详情 ──────────────────
    resp = client.get(f"/api/agent/runs/{resumed.id}", headers=headers)
    assert resp.status_code == 200, f"run detail -> {resp.status_code}: {resp.text[:200]}"
    detail = resp.json().get("data") if isinstance(resp.json(), dict) else resp.json()
    assert len(detail.get("logs") or []) > 0
    print(f"[OK] GET /api/agent/runs/{resumed.id} -> logs={len(detail['logs'])} status={detail['status']}")

    app.dependency_overrides.clear()
    db.close()
    print("\n=== 端到端落地验证全部通过 ===")


if __name__ == "__main__":
    main()
