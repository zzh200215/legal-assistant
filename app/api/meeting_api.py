from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.api_response import api_error, paginated_payload, should_passthrough_exception
from app.core.auth import get_current_user
from app.core.celery_app import celery_app
from app.core.database import get_db
from app.core.task_status import serialize_async_result
from app.models.user import User
from app.schemas.meeting import MeetingOut
from app.services.meeting_service import meeting_service
from app.services.oplog_service import oplog_service

router = APIRouter()


class MeetingCreateRequest(BaseModel):
    title: str
    transcript: str | None = None


class MeetingSummarizeRequest(BaseModel):
    async_mode: bool = False


@router.get("/")
def list_meetings(
    scope: str = Query("all", description="可见范围筛选：all/mine/department/organization/shared"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取用户的会议列表。"""
    from app.models.meeting import Meeting

    rows = db.query(Meeting).order_by(Meeting.created_at.desc()).all()
    visible_rows = [
        row for row in rows
        if meeting_service._can_access_meeting(
            row,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        ) and meeting_service.match_scope(
            row,
            user_id=current_user.id,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
            scope=scope,
        )
    ]
    total = len(visible_rows)
    meetings = visible_rows[(page - 1) * page_size : page * page_size]
    items = [
        {
            "id": m.id,
            "title": m.title,
            "organization_id": m.organization_id,
            "department_id": m.department_id,
            "status": m.status,
            "created_at": m.created_at,
        }
        for m in meetings
    ]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.post("/", response_model=MeetingOut)
def create_meeting(req: MeetingCreateRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        meeting = meeting_service.create(
            title=req.title,
            transcript=req.transcript,
            user_id=current_user.id,
            db=db,
        )
        return meeting
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "会议创建失败", code="MEETING_CREATE_FAILED", detail=str(e))


@router.post("/upload-image", response_model=MeetingOut)
def upload_meeting_image(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        meeting = meeting_service.create_from_uploaded_image(
            title=title,
            file=file,
            user_id=current_user.id,
            db=db,
        )
        return meeting
    except ValueError as e:
        raise api_error(400, str(e), code="MEETING_IMAGE_UPLOAD_INVALID")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "会议图片解析失败", code="MEETING_IMAGE_UPLOAD_FAILED", detail=str(e))


@router.post("/upload-audio", response_model=MeetingOut)
def upload_meeting_audio(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    transcript_text: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        meeting = meeting_service.create_from_uploaded_audio(
            title=title,
            file=file,
            transcript_text=transcript_text,
            user_id=current_user.id,
            db=db,
        )
        return meeting
    except ValueError as e:
        raise api_error(400, str(e), code="MEETING_AUDIO_UPLOAD_INVALID")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "会议音频上传失败", code="MEETING_AUDIO_UPLOAD_FAILED", detail=str(e))


@router.get("/{meeting_id}", response_model=MeetingOut)
def get_meeting(meeting_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    meeting = meeting_service.get(
        meeting_id,
        db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    if not meeting:
        raise api_error(404, "会议不存在", code="MEETING_NOT_FOUND")
    return meeting


@router.get("/{meeting_id}/summary")
def get_meeting_summary(meeting_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    meeting = meeting_service.get(
        meeting_id,
        db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    if not meeting:
        raise api_error(404, "会议不存在", code="MEETING_NOT_FOUND")

    summary = meeting_service.get_summary(
        meeting_id,
        db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    if not summary:
        raise api_error(404, "会议纪要不存在", code="MEETING_SUMMARY_NOT_FOUND")
    return meeting_service.serialize_summary(summary)


@router.get("/{meeting_id}/transcript")
def get_meeting_transcript(meeting_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    meeting = meeting_service.get(
        meeting_id,
        db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    if not meeting:
        raise api_error(404, "会议不存在", code="MEETING_NOT_FOUND")
    return meeting_service.get_transcript(meeting)


@router.post("/{meeting_id}/summarize")
async def summarize_meeting(
    meeting_id: int,
    req: MeetingSummarizeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        if req.async_mode:
            from app.tasks import summarize_meeting_task

            task = summarize_meeting_task.delay(meeting_id, current_user.id)
            oplog_service.log(
                module="async_task",
                action="meeting_summary_submitted",
                db=db,
                user_id=current_user.id,
                target_type="meeting",
                target_id=meeting_id,
                detail=f"task_id={task.id}",
            )
            return {
                "meeting_id": meeting_id,
                "task_id": task.id,
                "state": "PENDING",
                "async_mode": True,
            }

        summary = await meeting_service.summarize(meeting_id, db, user_id=current_user.id)
        return meeting_service.serialize_summary(summary)
    except ValueError as e:
        raise api_error(404, str(e), code="MEETING_NOT_FOUND")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "会议纪要生成失败", code="MEETING_SUMMARY_FAILED", detail=str(e))


@router.get("/task/{task_id}/status")
def get_meeting_task_status(task_id: str, current_user: User = Depends(get_current_user)):
    result = celery_app.AsyncResult(task_id)
    return serialize_async_result(result)


@router.post("/{meeting_id}/extract-decisions")
async def extract_decisions(meeting_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """决策事项单独抽取：区分讨论中和已确认。"""
    try:
        decisions = await meeting_service.extract_decisions(meeting_id, db, user_id=current_user.id)
        return {"meeting_id": meeting_id, "decisions": decisions}
    except ValueError as e:
        raise api_error(404, str(e), code="MEETING_NOT_FOUND")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "决策事项抽取失败", code="MEETING_DECISION_EXTRACT_FAILED", detail=str(e))


@router.post("/{meeting_id}/extract-topics")
async def extract_topics(meeting_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """议题抽取：识别会议讨论的主题及关键观点。"""
    try:
        topics = await meeting_service.extract_topics(meeting_id, db, user_id=current_user.id)
        return {"meeting_id": meeting_id, "topics": topics}
    except ValueError as e:
        raise api_error(404, str(e), code="MEETING_NOT_FOUND")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "议题抽取失败", code="MEETING_TOPIC_EXTRACT_FAILED", detail=str(e))


@router.post("/{meeting_id}/extract-tasks")
def extract_tasks_from_meeting(meeting_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        tasks = meeting_service.extract_tasks(meeting_id, current_user.id, db)
        return {
            "meeting_id": meeting_id,
            "created_tasks": len(tasks),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "assignee": t.assignee,
                    "due_date": t.due_date,
                    "priority": t.priority,
                }
                for t in tasks
            ],
        }
    except ValueError as e:
        raise api_error(400, str(e), code="MEETING_TASK_EXTRACT_INVALID")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "会议任务提取失败", code="MEETING_TASK_EXTRACT_FAILED", detail=str(e))
