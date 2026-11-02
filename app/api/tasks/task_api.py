from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.api_response import api_error, paginated_payload, should_passthrough_exception
from app.core.auth import get_current_user, verify_case_access
from app.core.database import get_db
from app.core.versioning import etag_for, parse_if_match
from app.models.legal import LegalCase
from app.models.legal_portal import LegalCaseMember
from app.models.org import OrganizationMember
from app.models.task import Task
from app.models.user import User
from app.schemas.task import (
    TaskBulkAction,
    TaskBulkReassign,
    TaskCommentCreate,
    TaskCommentOut,
    TaskCommentUpdate,
    TaskCreate,
    TaskLogOut,
    TaskOut,
    TaskUpdate,
)
from app.services.jobs.task_service import task_service

router = APIRouter()


def _accessible_case_ids(db: Session, case_ids: set[int], user: User) -> set[int]:
    """按 verify_case_access 的口径批量过滤：所属组织成员 + 严格案件成员。"""
    if not case_ids:
        return set()
    org_ids = {
        org_id
        for (org_id,) in db.query(OrganizationMember.organization_id)
        .filter(OrganizationMember.user_id == user.id)
        .all()
    }
    if not org_ids:
        return set()
    rows = (
        db.query(LegalCase.id, LegalCase.is_strict_mode)
        .filter(LegalCase.id.in_(case_ids), LegalCase.organization_id.in_(org_ids))
        .all()
    )
    accessible = {case_id for case_id, is_strict in rows if not is_strict}
    strict_ids = [case_id for case_id, is_strict in rows if is_strict]
    if strict_ids:
        accessible |= {
            case_id
            for (case_id,) in db.query(LegalCaseMember.case_id)
            .filter(
                LegalCaseMember.case_id.in_(strict_ids),
                LegalCaseMember.user_id == user.id,
                LegalCaseMember.revoked_at.is_(None),
            )
            .all()
        }
    return accessible


def _user_display_map(db: Session, user_ids: set[int | None]) -> dict[int, tuple[str | None, str | None]]:
    ids = {user_id for user_id in user_ids if user_id}
    if not ids:
        return {}
    return {
        user_id: (username, full_name)
        for user_id, username, full_name in db.query(
            User.id, User.username, User.full_name
        ).filter(User.id.in_(ids)).all()
    }


def _attach_case_titles(db: Session, payloads: list[dict], current_user: User) -> list[dict]:
    """批量补 case_title，避免任务列表按案件 N+1 查询。

    step83 起按 verify_case_access 的口径过滤：无权限的案件（含严格案件非成员）
    不返回标题，避免共享任务把案件名带给不该看的人。
    """
    case_ids = {item.get("case_id") for item in payloads if item.get("case_id")}
    titles = {}
    if case_ids:
        accessible = _accessible_case_ids(db, case_ids, current_user)
        if accessible:
            titles = {
                case_id: title
                for case_id, title in db.query(LegalCase.id, LegalCase.title).filter(
                    LegalCase.id.in_(accessible)
                ).all()
            }
    for item in payloads:
        item["case_title"] = titles.get(item.get("case_id"))
    return payloads


class ExtractFromDocRequest(BaseModel):
    document_id: int


class ExtractFromChatRequest(BaseModel):
    message: str


@router.post("/", response_model=TaskOut)
def create_task(req: TaskCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if req.case_id is not None:
        verify_case_access(req.case_id, current_user.id, db)
    try:
        task = task_service.create(
            title=req.title,
            user_id=current_user.id,
            db=db,
            description=req.description,
            assignee=req.assignee,
            assignee_user_id=req.assignee_user_id,
            collaborators=req.collaborators,
            collaborator_user_ids=req.collaborator_user_ids,
            due_date=req.due_date,
            priority=req.priority,
            progress=req.progress,
            case_id=req.case_id,
        )
        payload = _attach_case_titles(db, [task_service.serialize_task(task)], current_user)[0]
        return TaskOut.model_validate(payload)
    except ValueError as e:
        raise api_error(400, "负责人不合法", code="TASK_ASSIGNEE_INVALID", detail=str(e))
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "任务创建失败", code="TASK_CREATE_FAILED", detail=str(e))


@router.get("/")
def list_tasks(
    status: str | None = Query(None, description="按状态筛选：todo/in_progress/done/cancelled"),
    scope: str = Query("all", description="可见范围筛选：all/mine/participating/department/organization/shared"),
    source_type: str | None = Query(None, description="按来源筛选：document/meeting/chat/decompose"),
    source_id: int | None = Query(None, description="按来源对象 ID 筛选"),
    case_id: int | None = Query(None, description="按案件筛选（案件任务视图）"),
    overdue: bool = Query(False, description="只看已逾期（todo/in_progress 且截止日期早于现在）"),
    archived: bool = Query(False, description="只看已归档任务（默认只看未归档）"),
    q: str | None = Query(None, max_length=100, description="关键词（匹配标题/描述）"),
    sort: Literal["created_desc", "due_asc", "due_desc"] = Query(
        "created_desc", description="排序：created_desc/due_asc/due_desc"
    ),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows, total, stats = task_service.paginate_visible(
        db=db,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
        status=status,
        scope=scope,
        source_type=source_type,
        source_id=source_id,
        case_id=case_id,
        keyword=q,
        overdue=overdue,
        archived=archived,
        sort=sort,
    )
    payloads = _attach_case_titles(
        db, [task_service.serialize_task(task) for task in rows], current_user
    )
    items = [TaskOut.model_validate(payload).model_dump() for payload in payloads]
    return paginated_payload(
        items, total=total, page=page, page_size=page_size, stats=stats
    )


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


@router.post("/bulk-reassign")
def bulk_reassign_tasks(
    req: TaskBulkReassign,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return task_service.bulk_reassign(
            req.task_ids,
            db,
            user_id=current_user.id,
            assignee_user_id=req.assignee_user_id,
            organization_id=current_user.organization_id,
        )
    except ValueError as e:
        raise api_error(400, "负责人不合法", code="TASK_ASSIGNEE_INVALID", detail=str(e))


@router.post("/bulk-action")
def bulk_action_tasks(
    req: TaskBulkAction,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return task_service.bulk_action(
            req.task_ids,
            db,
            user_id=current_user.id,
            action=req.action,
            priority=req.priority,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
    except ValueError as e:
        raise api_error(400, "批量操作不合法", code="TASK_BULK_ACTION_INVALID", detail=str(e))


@router.get("/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
    response.headers["ETag"] = etag_for(task.version)
    payload = _attach_case_titles(db, [task_service.serialize_task(task)], current_user)[0]
    return TaskOut.model_validate(payload)


@router.put("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    req: TaskUpdate,
    if_match: str | None = Header(None, alias="If-Match"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        update_data = req.model_dump(exclude_unset=True)
        task = task_service.update(
            task_id,
            db,
            user_id=current_user.id,
            if_match_version=parse_if_match(if_match),
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
            **update_data,
        )
        payload = _attach_case_titles(db, [task_service.serialize_task(task)], current_user)[0]
        return TaskOut.model_validate(payload)
    except PermissionError as e:
        raise api_error(403, "无权修改该任务", code="TASK_EDIT_FORBIDDEN", detail=str(e))
    except ValueError as e:
        raise api_error(404, str(e), code="TASK_NOT_FOUND")
    except StaleDataError:
        # 乐观锁冲突（版本不匹配 / 并发修改）：透传给全局 handler 映射 409。
        raise
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "任务更新失败", code="TASK_UPDATE_FAILED", detail=str(e))


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(
    task_id: int,
    req: TaskUpdate,
    if_match: str | None = Header(None, alias="If-Match"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return update_task(task_id=task_id, req=req, if_match=if_match, db=db, current_user=current_user)


@router.post("/{task_id}/archive", response_model=TaskOut)
def archive_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        task = task_service.archive(task_id, db, user_id=current_user.id)
        payload = _attach_case_titles(db, [task_service.serialize_task(task)], current_user)[0]
        return TaskOut.model_validate(payload)
    except ValueError as e:
        raise api_error(404, str(e), code="TASK_NOT_FOUND")


@router.post("/{task_id}/unarchive", response_model=TaskOut)
def unarchive_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        task = task_service.unarchive(task_id, db, user_id=current_user.id)
        payload = _attach_case_titles(db, [task_service.serialize_task(task)], current_user)[0]
        return TaskOut.model_validate(payload)
    except ValueError as e:
        raise api_error(404, str(e), code="TASK_NOT_FOUND")


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
    sub_tasks, total = task_service.paginate_sub_tasks(
        task_id,
        db=db,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    payloads = _attach_case_titles(
        db, [task_service.serialize_task(task) for task in sub_tasks], current_user
    )
    items = [TaskOut.model_validate(payload).model_dump() for payload in payloads]
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
        names = _user_display_map(db, {row.user_id for row in rows})
        return [
            TaskCommentOut.model_validate(row).model_copy(update={
                "username": names.get(row.user_id, (None, None))[0],
                "full_name": names.get(row.user_id, (None, None))[1],
            })
            for row in rows
        ]
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
        row = task_service.add_comment(
            task_id,
            current_user.id,
            req.content,
            db,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        return TaskCommentOut.model_validate(row).model_copy(update={
            "username": current_user.username,
            "full_name": current_user.full_name,
        })
    except PermissionError as exc:
        raise api_error(403, "无权评论该任务", code="TASK_COMMENT_FORBIDDEN", detail=str(exc))
    except ValueError as exc:
        detail = str(exc)
        if detail == "Task not found":
            raise api_error(404, "任务不存在", code="TASK_NOT_FOUND", detail=detail)
        raise api_error(400, "评论内容不合法", code="TASK_COMMENT_INVALID", detail=detail)


@router.patch("/{task_id}/comments/{comment_id}", response_model=TaskCommentOut)
def update_task_comment(
    task_id: int,
    comment_id: int,
    req: TaskCommentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        row = task_service.update_comment(
            task_id, comment_id, req.content, db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        return TaskCommentOut.model_validate(row).model_copy(update={
            "username": current_user.username,
            "full_name": current_user.full_name,
        })
    except PermissionError as exc:
        raise api_error(403, "无权修改该评论", code="TASK_COMMENT_FORBIDDEN", detail=str(exc))
    except ValueError as exc:
        detail = str(exc)
        if detail in ("Task not found", "Comment not found"):
            raise api_error(404, "评论不存在", code="TASK_COMMENT_NOT_FOUND", detail=detail)
        raise api_error(400, "评论内容不合法", code="TASK_COMMENT_INVALID", detail=detail)


@router.delete("/{task_id}/comments/{comment_id}", status_code=204)
def delete_task_comment(
    task_id: int,
    comment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        task_service.delete_comment(
            task_id, comment_id, db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        return Response(status_code=204)
    except PermissionError as exc:
        raise api_error(403, "无权删除该评论", code="TASK_COMMENT_FORBIDDEN", detail=str(exc))
    except ValueError as exc:
        detail = str(exc)
        if detail in ("Task not found", "Comment not found"):
            raise api_error(404, "评论不存在", code="TASK_COMMENT_NOT_FOUND", detail=detail)
        raise api_error(400, "评论操作失败", code="TASK_COMMENT_INVALID", detail=detail)


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
        names = _user_display_map(db, {row.user_id for row in rows})
        return [
            TaskLogOut.model_validate(row).model_copy(update={
                "username": names.get(row.user_id, (None, None))[0],
                "full_name": names.get(row.user_id, (None, None))[1],
            })
            for row in rows
        ]
    except ValueError as exc:
        raise api_error(404, "任务不存在", code="TASK_NOT_FOUND", detail=str(exc))
