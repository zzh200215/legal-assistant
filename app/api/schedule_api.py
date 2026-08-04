from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.api_response import api_error, should_passthrough_exception
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.schedule import ScheduledWorkflowCreate, ScheduledWorkflowOut, ScheduledWorkflowUpdate, WorkflowExecutionOut
from app.services.scheduler_service import scheduler_service

router = APIRouter()


@router.get("/", response_model=list[ScheduledWorkflowOut])
def list_schedules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [ScheduledWorkflowOut(**scheduler_service.serialize_schedule(item)) for item in scheduler_service.list_schedules(db=db, user=current_user)]


@router.post("/", response_model=ScheduledWorkflowOut)
def create_schedule(req: ScheduledWorkflowCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        schedule = scheduler_service.create_schedule(db=db, user=current_user, request=req)
        return ScheduledWorkflowOut(**scheduler_service.serialize_schedule(schedule))
    except ValueError as exc:
        raise api_error(400, "计划创建失败", code="SCHEDULE_CREATE_INVALID", detail=str(exc))


@router.patch("/{schedule_id:int}", response_model=ScheduledWorkflowOut)
def update_schedule(schedule_id: int, req: ScheduledWorkflowUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        schedule = scheduler_service.update_schedule(schedule_id, db=db, user=current_user, request=req)
        return ScheduledWorkflowOut(**scheduler_service.serialize_schedule(schedule))
    except ValueError as exc:
        raise api_error(404, "计划不存在或无法更新", code="SCHEDULE_UPDATE_INVALID", detail=str(exc))


@router.post("/{schedule_id:int}/run", response_model=WorkflowExecutionOut)
def run_schedule(schedule_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        execution = scheduler_service.start_manual_run(schedule_id, db=db, user=current_user)
        from app.tasks import scheduled_workflow_run_task

        task = scheduled_workflow_run_task.delay(execution.id)
        execution.celery_task_id = task.id
        db.commit()
        db.refresh(execution)
        return WorkflowExecutionOut(**scheduler_service.serialize_execution(execution))
    except ValueError as exc:
        raise api_error(404, "计划不存在", code="SCHEDULE_NOT_FOUND", detail=str(exc))
    except Exception as exc:
        if should_passthrough_exception(exc):
            raise
        raise api_error(500, "计划试跑提交失败", code="SCHEDULE_RUN_SUBMIT_FAILED", detail=str(exc))


@router.get("/executions/", response_model=list[WorkflowExecutionOut])
def list_executions(
    schedule_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return [WorkflowExecutionOut(**scheduler_service.serialize_execution(item)) for item in scheduler_service.list_executions(db=db, user=current_user, schedule_id=schedule_id, limit=limit)]
