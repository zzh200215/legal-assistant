from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.api_response import api_error, paginated_payload, should_passthrough_exception
from app.core.database import get_db
from app.models.agent import AgentRun
from app.models.user import User
from app.schemas.agent import (
    AgentApprovalDecisionRequest,
    AgentApprovalResumeRequest,
    AgentRunCancelRequest,
    AgentApprovalRequestOut,
    AgentPlanPreviewRequest,
    AgentPlanPreviewResponse,
    AgentRunDetailOut,
    AgentRunHistoryOut,
    AgentRunRequest,
    AgentRunResponse,
    ToolCallLogOut,
)
from app.services.agent.agent_approval_service import agent_approval_service
from app.services.agent.agent_registry import AGENT_REGISTRY_VERSION, TASK_PROTOCOL_VERSION, get_supervisor_registration, list_agent_registrations
from app.services.agent.agent_harness_service import get_harness_profile
from app.services.agent.agent_skill_registry import SKILL_REGISTRY_VERSION, get_agent_skill, list_agent_skills, resolve_agent_skill
from app.services.agent.agent_service import agent_service
from app.services.observability.oplog_service import oplog_service

router = APIRouter()


def _serialize_log(log) -> ToolCallLogOut:
    return ToolCallLogOut.model_validate(log)


def _serialize_run(run: AgentRun, logs=None) -> AgentRunDetailOut:
    serialized = agent_service.serialize_run(run)
    return AgentRunDetailOut(
        id=run.id,
        user_id=run.user_id,
        session_id=run.session_id,
        goal=run.goal,
        status=run.status,
        result=run.result,
        final_answer=run.final_answer,
        artifacts=serialized.get("artifacts") or {},
        supervisor_plan=serialized.get("supervisor_plan") or {},
        last_observation=run.last_observation,
        failure_reason=run.failure_reason,
        total_steps=run.total_steps,
        error=run.error,
        created_at=run.created_at,
        completed_at=run.completed_at,
        logs=[_serialize_log(item) for item in (logs or [])],
    )


@router.post("/run", response_model=AgentRunResponse)
async def run_agent(
    req: AgentRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        agent_run = await agent_service.run(
            goal=req.goal,
            user_id=current_user.id,
            db=db,
            session_id=req.session_id,
            max_steps=req.max_steps,
        )
        logs = agent_service.get_run_logs(agent_run.id, db, user_id=current_user.id)
        return AgentRunResponse(
            run_id=agent_run.id,
            status=agent_run.status,
            result=agent_run.result,
            final_answer=agent_run.final_answer,
            artifacts=agent_service.serialize_run(agent_run).get("artifacts") or {},
            supervisor_plan=agent_service.serialize_run(agent_run).get("supervisor_plan") or {},
            failure_reason=agent_run.failure_reason,
            error=agent_run.error,
            logs=[_serialize_log(item) for item in logs],
        )
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "Agent 执行失败", code="AGENT_RUN_FAILED", detail=str(e))


@router.post("/preview", response_model=AgentPlanPreviewResponse)
async def preview_agent_plan(
    req: AgentPlanPreviewRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        return await agent_service.preview_plan(
            goal=req.goal,
            user_id=current_user.id,
            max_steps=req.max_steps,
        )
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "计划预览失败", code="AGENT_PREVIEW_FAILED", detail=str(e))


@router.get("/registry")
def get_agent_registry(current_user: User = Depends(get_current_user)):
    """Expose canonical role contracts without exposing compatibility aliases."""
    _ = current_user
    return {
        "registry_version": AGENT_REGISTRY_VERSION,
        "task_protocol_version": TASK_PROTOCOL_VERSION,
        "supervisor": get_supervisor_registration(),
        "items": list_agent_registrations(),
    }


@router.get("/harness")
def get_agent_harness(current_user: User = Depends(get_current_user)):
    """Expose the server-enforced Agent lifecycle and safety controls."""
    _ = current_user
    return get_harness_profile()


@router.get("/skills")
def list_skills(
    goal: str | None = Query(None, max_length=2000, description="可选：根据任务目标返回推荐 Skill"),
    current_user: User = Depends(get_current_user),
):
    _ = current_user
    return {
        "registry_version": SKILL_REGISTRY_VERSION,
        "recommended_skill": resolve_agent_skill(goal or ""),
        "items": list_agent_skills(),
    }


@router.get("/skills/{skill_id}")
def get_skill(skill_id: str, current_user: User = Depends(get_current_user)):
    _ = current_user
    skill = get_agent_skill(skill_id)
    if not skill:
        raise api_error(404, "Skill 不存在", code="AGENT_SKILL_NOT_FOUND")
    return skill


@router.get("/runs")
def list_runs(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    artifact_type: str | None = Query(None, description="按产出对象过滤：document/meeting/task/email"),
    artifact_id: int | None = Query(None, ge=1, description="产出对象 ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if artifact_type and artifact_id:
        matched_runs, total = agent_service.list_runs_by_artifact(
            db=db,
            user_id=current_user.id,
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            page=page,
            page_size=page_size,
        )
        runs = matched_runs
    else:
        query = db.query(AgentRun).filter(AgentRun.user_id == current_user.id)
        total = query.count()
        runs = query.order_by(AgentRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        AgentRunHistoryOut(
            id=run.id,
            goal=run.goal,
            status=run.status,
            result=(run.result or "")[:200] or None,
            final_answer=(run.final_answer or "")[:200] or None,
            failure_reason=run.failure_reason,
            total_steps=run.total_steps,
            created_at=run.created_at,
            completed_at=run.completed_at,
        )
        for run in runs
    ]
    return paginated_payload(
        [item.model_dump() for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/runs/{run_id}", response_model=AgentRunDetailOut)
def get_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = agent_service.get_run(run_id, db, user_id=current_user.id)
    if not run:
        raise api_error(404, "运行记录不存在", code="AGENT_RUN_NOT_FOUND")
    logs = agent_service.get_run_logs(run_id, db, user_id=current_user.id)
    return _serialize_run(run, logs=logs)


@router.get("/runs/{run_id}/logs", response_model=list[ToolCallLogOut])
def get_run_logs(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    logs = agent_service.get_run_logs(run_id, db, user_id=current_user.id)
    return [_serialize_log(log) for log in logs]


@router.post("/runs/{run_id}/cancel", response_model=AgentRunDetailOut)
def cancel_run(run_id: int, req: AgentRunCancelRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        run = agent_service.request_cancel(run_id, db=db, user_id=current_user.id, reason=req.reason)
        oplog_service.log(module="agent", action="run_cancel_requested", db=db, user_id=current_user.id, target_type="agent_run", target_id=run.id, detail=req.reason or "")
        return _serialize_run(run, logs=agent_service.get_run_logs(run.id, db, user_id=current_user.id))
    except ValueError as exc:
        raise api_error(400, "取消执行失败", code="AGENT_CANCEL_INVALID", detail=str(exc))


@router.get("/metrics")
def get_agent_metrics(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return agent_service.get_run_metrics(db=db, user_id=current_user.id, days=days)


@router.post("/runs/{run_id}/retry", response_model=AgentRunResponse)
async def retry_run(run_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    previous = agent_service.get_run(run_id, db, user_id=current_user.id)
    if not previous:
        raise api_error(404, "运行记录不存在", code="AGENT_RUN_NOT_FOUND")
    if previous.status not in {"error", "cancelled", "completed"}:
        raise api_error(400, "当前运行不可重跑", code="AGENT_RETRY_INVALID")
    run = await agent_service.run(goal=previous.goal, user_id=current_user.id, db=db, session_id=previous.session_id, max_steps=max(1, min(previous.total_steps or 5, 10)))
    oplog_service.log(module="agent", action="run_retried", db=db, user_id=current_user.id, target_type="agent_run", target_id=run.id, detail=f"source_run_id={previous.id}")
    logs = agent_service.get_run_logs(run.id, db, user_id=current_user.id)
    return AgentRunResponse(run_id=run.id, status=run.status, result=run.result, final_answer=run.final_answer, artifacts=agent_service.serialize_run(run).get("artifacts") or {}, supervisor_plan=agent_service.serialize_run(run).get("supervisor_plan") or {}, failure_reason=run.failure_reason, error=run.error, logs=[_serialize_log(item) for item in logs])


@router.get("/approvals", response_model=list[AgentApprovalRequestOut])
def list_approvals(
    status: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return agent_approval_service.list_requests(db=db, user_id=current_user.id, status=status)


@router.post("/approvals/{approval_id}/decision", response_model=AgentApprovalRequestOut)
def decide_approval(
    approval_id: int,
    req: AgentApprovalDecisionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return agent_approval_service.decide_request(
            db=db,
            approval_id=approval_id,
            user_id=current_user.id,
            approved=req.approved,
            decision_note=req.decision_note,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "Approval request not found":
            raise api_error(404, "审批请求不存在", code="AGENT_APPROVAL_NOT_FOUND", detail=detail)
        raise api_error(400, "审批请求状态不合法", code="AGENT_APPROVAL_INVALID", detail=detail)


@router.post("/approvals/{approval_id}/resume", response_model=AgentRunDetailOut)
async def resume_approval_run(
    approval_id: int,
    req: AgentApprovalResumeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        approval = agent_approval_service.get_request(db=db, approval_id=approval_id, user_id=current_user.id)
        if not approval:
            raise ValueError("Approval request not found")
        if approval.status == "pending":
            agent_approval_service.decide_request(
                db=db,
                approval_id=approval_id,
                user_id=current_user.id,
                approved=True,
                decision_note=req.decision_note,
            )
        run = await agent_service.resume_after_approval(
            approval_id=approval_id,
            user_id=current_user.id,
            db=db,
        )
        logs = agent_service.get_run_logs(run.id, db, user_id=current_user.id)
        return _serialize_run(run, logs=logs)
    except ValueError as exc:
        detail = str(exc)
        if detail == "Approval request not found":
            raise api_error(404, "审批请求不存在", code="AGENT_APPROVAL_NOT_FOUND", detail=detail)
        raise api_error(400, "审批恢复失败", code="AGENT_APPROVAL_RESUME_INVALID", detail=detail)
