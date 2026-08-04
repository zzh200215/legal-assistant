from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.api_response import api_error, paginated_payload, should_passthrough_exception
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.task import Task
from app.models.user import User
from app.schemas.task import TaskCommentCreate, TaskCommentOut, TaskCreate, TaskLogOut, TaskOut, TaskUpdate
from app.services.task_service import task_service

router = APIRouter()


class ExtractFromDocRequest(BaseModel):
    document_id: int


class ExtractFromChatRequest(BaseModel):
    message: str


@router.post("/", response_model=TaskOut)
def create_task(req: TaskCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        task = task_service.create(
            title=req.title,
            user_id=current_user.id,
            db=db,
            description=req.description,
            assignee=req.assignee,
            collaborators=req.collaborators,
            due_date=req.due_date,
            priority=req.priority,
            progress=req.progress,
        )
        return TaskOut.model_validate(task_service.serialize_task(task))
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "任务创建失败", code="TASK_CREATE_FAILED", detail=str(e))


@router.get("/")
def list_tasks(
    status: str | None = Query(None, description="按状态筛选：todo/in_progress/done/cancelled"),
    scope: str = Query("all", description="可见范围筛选：all/mine/department/organization/shared"),
    source_type: str | None = Query(None, description="按来源筛选：document/meeting/chat/decompose"),
    source_id: int | None = Query(None, description="按来源对象 ID 筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = task_service.list_visible(
        db=db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
        status=status,
        scope=scope,
        source_type=source_type,
        source_id=source_id,
    )
    total = len(rows)
    tasks = rows[(page - 1) * page_size : page * page_size]
    items = [TaskOut.model_validate(task_service.serialize_task(task)).model_dump() for task in tasks]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.post("/extract-from-document")
async def extract_from_document(req: ExtractFromDocRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """从文档提取待办任务并写入 tasks 表。"""
    try:
        tasks = await task_service.extract_from_document(req.document_id, current_user.id, db)
        return {
            "document_id": req.document_id,
            "created_tasks": len(tasks),
            "tasks": [{"id": t.id, "title": t.title, "description": t.description} for t in tasks],
        }
    except ValueError as e:
        raise api_error(404, str(e), code="DOCUMENT_NOT_FOUND")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "从文档提取任务失败", code="TASK_EXTRACT_FROM_DOCUMENT_FAILED", detail=str(e))


@router.post("/extract-from-chat")
async def extract_from_chat(req: ExtractFromChatRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """从聊天消息中识别待办任务。"""
    try:
        tasks = await task_service.extract_from_chat(req.message, current_user.id, db)
        return {
            "created_tasks": len(tasks),
            "tasks": [{"id": t.id, "title": t.title, "description": t.description} for t in tasks],
        }
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "从聊天提取任务失败", code="TASK_EXTRACT_FROM_CHAT_FAILED", detail=str(e))


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = task_service.get(
        task_id,
        db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    if not task:
        raise api_error(404, "任务不存在", code="TASK_NOT_FOUND")
    return TaskOut.model_validate(task_service.serialize_task(task))


@router.put("/{task_id}", response_model=TaskOut)
def update_task(task_id: int, req: TaskUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        update_data = req.model_dump(exclude_unset=True)
        task = task_service.update(task_id, db, user_id=current_user.id, **update_data)
        return TaskOut.model_validate(task_service.serialize_task(task))
    except ValueError as e:
        raise api_error(404, str(e), code="TASK_NOT_FOUND")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "任务更新失败", code="TASK_UPDATE_FAILED", detail=str(e))


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(task_id: int, req: TaskUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return update_task(task_id=task_id, req=req, db=db, current_user=current_user)


@router.post("/{task_id}/decompose")
async def decompose_task(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """任务拆解：将大目标拆成子任务。"""
    try:
        sub_tasks = await task_service.decompose(task_id, current_user.id, db)
        return {
            "parent_task_id": task_id,
            "created_sub_tasks": len(sub_tasks),
            "sub_tasks": [{"id": t.id, "title": t.title, "description": t.description} for t in sub_tasks],
        }
    except ValueError as e:
        raise api_error(404, str(e), code="TASK_NOT_FOUND")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "任务拆解失败", code="TASK_DECOMPOSE_FAILED", detail=str(e))


@router.get("/{task_id}/sub-tasks")
def get_sub_tasks(
    task_id: int,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取子任务列表。"""
    parent = task_service.get(
        task_id,
        db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    if not parent:
        raise api_error(404, "任务不存在", code="TASK_NOT_FOUND")
    all_rows = db.query(Task).filter(Task.parent_id == task_id).order_by(Task.created_at.desc()).all()
    visible_rows = [
        row for row in all_rows
        if task_service._can_access_task(
            row,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
    ]
    total = len(visible_rows)
    sub_tasks = visible_rows[(page - 1) * page_size : page * page_size]
    items = [TaskOut.model_validate(task_service.serialize_task(task)).model_dump() for task in sub_tasks]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/{task_id}/comments", response_model=list[TaskCommentOut])
def list_task_comments(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        rows = task_service.list_comments(
            task_id,
            db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        return [TaskCommentOut.model_validate(row) for row in rows]
    except ValueError as exc:
        raise api_error(404, "任务不存在", code="TASK_NOT_FOUND", detail=str(exc))


@router.post("/{task_id}/comments", response_model=TaskCommentOut)
def add_task_comment(
    task_id: int,
    req: TaskCommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = task_service.add_comment(task_id, current_user.id, req.content, db)
        return TaskCommentOut.model_validate(row)
    except ValueError as exc:
        detail = str(exc)
        if detail == "Task not found":
            raise api_error(404, "任务不存在", code="TASK_NOT_FOUND", detail=detail)
        raise api_error(400, "评论内容不合法", code="TASK_COMMENT_INVALID", detail=detail)


@router.get("/{task_id}/logs", response_model=list[TaskLogOut])
def list_task_logs(task_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        rows = task_service.list_logs(
            task_id,
            db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        return [TaskLogOut.model_validate(row) for row in rows]
    except ValueError as exc:
        raise api_error(404, "任务不存在", code="TASK_NOT_FOUND", detail=str(exc))
