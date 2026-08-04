from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.api_response import api_error, paginated_payload
from app.core.auth import get_current_user, require_admin_user
from app.core.database import get_db
from app.models.user import User
from app.services.analytics_service import analytics_service
from app.services.document_qa_service import document_qa_service

router = APIRouter()


class RetryTaskRunRequest(BaseModel):
    source: str
    task_key: str


class ResolveFeedbackRequest(BaseModel):
    resolution_note: str | None = None


class FeedbackEvalBundleRequest(BaseModel):
    days: int = 30


@router.get("/tokens/my-stats")
def my_token_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return analytics_service.get_user_token_stats(current_user.id, db, days=days)


@router.get("/tokens/global-stats")
def global_token_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.get_global_token_stats(db, days=days)


@router.get("/experiments/overview")
def experiment_overview(
    days: int = Query(30, description="Prompt 版本流量统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.get_experiment_overview(db=db, days=days)


@router.get("/llm-calls")
def list_llm_calls(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    module_name: str | None = Query(None, description="模块名称"),
    action: str | None = Query(None, description="调用动作"),
    status: str | None = Query(None, description="success/error"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    rows = analytics_service.list_llm_calls(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
        module_name=module_name,
        action=action,
        status=status,
    )
    total = len(rows)
    sliced_rows = rows[(page - 1) * page_size : page * page_size]
    items = [
        {
            "id": row.id,
            "module_name": row.module_name,
            "action": row.action,
            "model_name": row.model_name,
            "prompt_template": row.prompt_template,
            "prompt_version": row.prompt_version,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "duration_ms": row.duration_ms,
            "status": row.status,
            "routing_role": row.routing_role,
            "routing_stage": row.routing_stage,
            "error_message": row.error_message if current_user.role == "admin" else None,
            "request_excerpt": row.request_excerpt if current_user.role == "admin" else None,
            "response_excerpt": row.response_excerpt if current_user.role == "admin" else None,
            "created_at": row.created_at,
        }
        for row in sliced_rows
    ]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/llm-calls/stats")
def llm_call_stats(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    module_name: str | None = Query(None, description="模块名称"),
    action: str | None = Query(None, description="调用动作"),
    status: str | None = Query(None, description="success/error"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    return analytics_service.get_llm_call_stats(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        module_name=module_name,
        action=action,
        status=status,
    )


@router.get("/llm-billing/stats")
def llm_billing_stats(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    module_name: str | None = Query(None, description="模块名称"),
    action: str | None = Query(None, description="调用动作"),
    status: str | None = Query(None, description="success/error"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    return analytics_service.get_llm_billing_stats(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        module_name=module_name,
        action=action,
        status=status,
    )


@router.get("/llm-routing/stats")
def llm_routing_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.get_llm_routing_stats(
        db=db,
        include_all_users=True,
        days=days,
    )


@router.get("/llm-routing/health")
def llm_routing_health(
    hours: int = Query(1, ge=1, le=24, description="统计窗口小时数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.get_llm_routing_health(db=db, hours=hours)


@router.get("/llm-pricing")
def llm_pricing(current_user: User = Depends(get_current_user)):
    return analytics_service.get_model_pricing()


@router.get("/oplogs")
def list_operation_logs(
    module: str | None = Query(None, description="按模块筛选"),
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")
    if module == "system" and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    logs = analytics_service.list_operation_logs(
        db,
        user_id=current_user.id,
        module=module,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
    )
    total = len(logs)
    sliced_logs = logs[(page - 1) * page_size : page * page_size]
    items = [
        {
            "id": log.id,
            "module": log.module,
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "created_at": log.created_at,
        }
        for log in sliced_logs
    ]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/oplogs/stats")
def operation_stats(
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return analytics_service.get_operation_stats(current_user.id, db, days=days)


@router.get("/alerts")
def list_alerts(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    source: str | None = Query(None, description="async_task 或 agent"),
    category: str | None = Query(None, description="告警分类"),
    severity: str | None = Query(None, description="告警级别"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    rows = analytics_service.list_alerts(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
        source=source,
        category=category,
        severity=severity,
    )
    total = len(rows)
    items = rows[(page - 1) * page_size : page * page_size]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/alerts/stats")
def alert_stats(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    source: str | None = Query(None, description="async_task 或 agent"),
    category: str | None = Query(None, description="告警分类"),
    severity: str | None = Query(None, description="告警级别"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    return analytics_service.get_alert_stats(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        source=source,
        category=category,
        severity=severity,
    )


@router.get("/task-runs")
def list_task_runs(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    source: str | None = Query(None, description="async_task 或 agent"),
    status: str | None = Query(None, description="pending/running/succeeded/failed"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    rows = analytics_service.list_task_runs(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
        source=source,
        status=status,
    )
    total = len(rows)
    items = rows[(page - 1) * page_size : page * page_size]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.post("/task-runs/retry")
def retry_task_run(
    req: RetryTaskRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return analytics_service.retry_task_run(
            db=db,
            source=req.source,
            task_key=req.task_key,
            user_id=current_user.id,
        )
    except ValueError as e:
        raise api_error(404, "任务运行记录不存在或不可重试", code="TASK_RUN_RETRY_NOT_AVAILABLE", detail=str(e))


@router.get("/feedback")
def list_feedback(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    feedback_value: str | None = Query(None, description="positive 或 negative"),
    feedback_status: str | None = Query(None, description="open 或 resolved"),
    source: str | None = Query(None, description="document/chat/ws_chat"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    rows = analytics_service.list_feedback_records(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        limit=1000,
        feedback_value=feedback_value,
        feedback_status=feedback_status,
        source=source,
    )
    total = len(rows)
    sliced_rows = rows[(page - 1) * page_size : page * page_size]
    items = [
        {
            "id": row.id,
            "document_id": row.document_id,
            "document_title": row.document.title if row.document else None,
            "user_id": row.user_id,
            "question": row.question,
            "answer": row.answer,
            "source": row.source,
            "feedback_value": row.feedback_value,
            "feedback_reason": row.feedback_reason,
            "feedback_note": row.feedback_note,
            "feedback_status": row.feedback_status,
            "feedback_created_at": row.feedback_created_at,
            "feedback_resolved_at": row.feedback_resolved_at,
            "feedback_resolution_note": row.feedback_resolution_note,
            "feedback_resolved_by": row.feedback_resolved_by,
            "latency_ms": row.latency_ms,
            "created_at": row.created_at,
        }
        for row in sliced_rows
    ]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/qa-replays")
def list_qa_replays(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    source: str | None = Query(None, description="document/chat/ws_chat"),
    feedback_status: str | None = Query(None, description="open 或 resolved"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    payload = analytics_service.list_qa_replays(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        page=page,
        page_size=page_size,
        source=source,
        feedback_status=feedback_status,
    )
    return paginated_payload(
        payload["items"],
        total=payload["total"],
        page=payload["page"],
        page_size=payload["page_size"],
    )


@router.get("/feedback/stats")
def feedback_stats(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    feedback_value: str | None = Query(None, description="positive 或 negative"),
    feedback_status: str | None = Query(None, description="open 或 resolved"),
    source: str | None = Query(None, description="document/chat/ws_chat"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")

    return analytics_service.get_feedback_stats(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
        feedback_value=feedback_value,
        feedback_status=feedback_status,
        source=source,
    )


@router.post("/feedback/{qa_record_id}/resolve")
def resolve_feedback(
    qa_record_id: int,
    req: ResolveFeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    try:
        record = document_qa_service.resolve_feedback(
            qa_record_id=qa_record_id,
            resolver_id=current_user.id,
            db=db,
            resolution_note=req.resolution_note,
        )
        analytics_service.create_operation_log(
            module="document",
            action="document_qa_feedback_resolved",
            db=db,
            user_id=current_user.id,
            target_type="document_qa_record",
            target_id=record.id,
            detail=f"status={record.feedback_status}; document_id={record.document_id}",
        )
        return {
            "id": record.id,
            "feedback_status": record.feedback_status,
            "feedback_resolved_at": record.feedback_resolved_at,
            "feedback_resolution_note": record.feedback_resolution_note,
            "feedback_resolved_by": record.feedback_resolved_by,
        }
    except ValueError as e:
        detail = str(e)
        if detail == "QA record not found":
            raise api_error(404, "问答记录不存在", code="QA_RECORD_NOT_FOUND", detail=detail)
        raise api_error(400, "反馈处理失败", code="DOCUMENT_QA_FEEDBACK_RESOLVE_INVALID", detail=detail)


@router.get("/tool-health")
def tool_health(
    scope: str = Query("mine", description="mine 或 all"),
    days: int = Query(30, description="统计天数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    include_all_users = scope == "all"
    if include_all_users and current_user.role != "admin":
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")
    return analytics_service.get_tool_health(
        db=db,
        user_id=current_user.id,
        include_all_users=include_all_users,
        days=days,
    )


@router.post("/feedback/export-eval-bundle")
def export_feedback_eval_bundle(
    req: FeedbackEvalBundleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    return analytics_service.export_feedback_eval_bundle(
        db=db,
        user_id=current_user.id,
        include_all_users=True,
        days=req.days,
    )


@router.post("/oplogs")
def create_operation_log(
    request: Request,
    module: str,
    action: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    target_type: str | None = None,
    target_id: int | None = None,
    detail: str | None = None,
):
    ip_address = request.client.host if request.client else None
    log = analytics_service.create_operation_log(
        module=module,
        action=action,
        db=db,
        user_id=current_user.id,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip_address=ip_address,
    )
    return {"id": log.id, "detail": "已记录"}
