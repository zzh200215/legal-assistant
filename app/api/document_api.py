from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.api_response import api_error, paginated_payload, should_passthrough_exception
from app.core.celery_app import celery_app
from app.core.database import get_db
from app.core.task_status import serialize_async_result
from app.models.user import User
from app.models.document import Document
from app.schemas.document import (
    DocumentOut,
    DocumentParseJobOut,
    DocumentQARecordOut,
    KnowledgeBaseOut,
    DocumentVisualAnalyzeOut,
    DocumentVisualAnalyzeRequest,
)
from app.services.analysis_service import analysis_service
from app.services.document_governance_service import document_governance_service
from app.services.document_delivery_service import DocumentDeliveryError, document_delivery_service
from app.services.document_job_service import document_job_service
from app.services.document_qa_service import document_qa_service
from app.services.document_service import document_service
from app.services.oplog_service import oplog_service
from app.services.data_permission_service import data_permission_service
from app.services.task_service import task_service

router = APIRouter()


class DocumentQAFeedbackRequest(BaseModel):
    feedback_value: str
    feedback_reason: str | None = None
    feedback_note: str | None = None


class DocumentQAFeedbackResolveRequest(BaseModel):
    resolution_note: str | None = None


class DocumentBatchUploadOut(BaseModel):
    documents: list[DocumentOut]
    count: int


class KnowledgeBaseCreateRequest(BaseModel):
    name: str
    description: str | None = None
    category: str | None = None
    permission_scope: str = "private"


class DocumentQAReplayOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    id: int
    document_id: int
    document_title: str | None = None
    user_id: int
    session_id: int | None = None
    question: str
    answer: str
    citations: list[dict] = []
    hit_chunks: list[dict] = []
    model_name: str | None = None
    latency_ms: int | None = None
    source: str
    feedback_value: str | None = None
    feedback_reason: str | None = None
    feedback_note: str | None = None
    feedback_status: str | None = None
    feedback_created_at: str | None = None
    feedback_resolved_at: str | None = None
    feedback_resolution_note: str | None = None
    feedback_resolved_by: int | None = None
    created_at: str | None = None


class DocumentDownloadPolicyRequest(BaseModel):
    download_enabled: bool | None = None
    watermark_required: bool | None = None


@router.get("/")
def list_documents(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    knowledge_base_id: int | None = Query(None),
    classification: str | None = Query(None),
    sensitivity_level: str | None = Query(None),
    connector_id: int | None = Query(None),
    q: str | None = Query(None, description="标题检索"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    docs = document_service.list_documents(
        db=db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
        knowledge_base_id=knowledge_base_id,
        classification=classification,
        sensitivity_level=sensitivity_level,
        connector_id=connector_id,
        query=q,
    )
    total = len(docs)
    page_rows = docs[(page - 1) * page_size : page * page_size]
    items = []
    for doc in page_rows:
        metadata = document_service._parse_metadata_json(doc.metadata_json)
        items.append(
            {
                "id": doc.id,
                "title": doc.title,
                "file_type": doc.file_type,
                "knowledge_base_id": doc.knowledge_base_id,
                "connector_id": metadata.get("connector_id"),
                "connector_name": metadata.get("connector_name"),
                "version_number": doc.version_number,
                "classification": doc.classification,
                "permission_scope": doc.permission_scope,
                "sensitivity_level": doc.sensitivity_level,
                "status": doc.status,
                "summary": doc.summary[:100] if doc.summary else None,
                "created_at": doc.created_at,
            }
        )
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseOut])
def list_knowledge_bases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return document_governance_service.list_knowledge_bases(
        db=db,
        user_id=current_user.id,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )


@router.post("/knowledge-bases", response_model=KnowledgeBaseOut)
def create_knowledge_base(
    req: KnowledgeBaseCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return document_governance_service.get_or_create_knowledge_base(
        db=db,
        user_id=current_user.id,
        name=req.name,
        category=req.category,
        description=req.description,
        permission_scope=req.permission_scope,
    )


@router.post("/upload", response_model=DocumentOut)
def upload_document(
    file: UploadFile = File(...),
    async_mode: bool = Query(False, description="是否异步解析"),
    knowledge_base_name: str | None = Form(None),
    knowledge_base_category: str | None = Form(None),
    classification: str | None = Form(None),
    tags: str | None = Form(None),
    permission_scope: str = Form("private"),
    sensitivity_level: str = Form("internal"),
    permission_users: str | None = Form(None),
    permission_roles: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return document_service.upload(
            file,
            user_id=current_user.id,
            db=db,
            async_mode=async_mode,
            knowledge_base_name=knowledge_base_name,
            knowledge_base_category=knowledge_base_category,
            classification=classification,
            tags=[item.strip() for item in (tags or "").split(",") if item.strip()],
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level,
            permission_users=[item.strip() for item in (permission_users or "").split(",") if item.strip()],
            permission_roles=[item.strip() for item in (permission_roles or "").split(",") if item.strip()],
        )
    except ValueError as e:
        raise api_error(400, str(e), code="DOCUMENT_UPLOAD_INVALID")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "文档上传失败", code="DOCUMENT_UPLOAD_FAILED", detail=str(e))


@router.post("/batch-upload", response_model=DocumentBatchUploadOut)
def batch_upload_documents(
    files: list[UploadFile] = File(...),
    async_mode: bool = Query(False, description="是否异步解析"),
    knowledge_base_name: str | None = Form(None),
    knowledge_base_category: str | None = Form(None),
    classification: str | None = Form(None),
    tags: str | None = Form(None),
    permission_scope: str = Form("private"),
    sensitivity_level: str = Form("internal"),
    permission_users: str | None = Form(None),
    permission_roles: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    documents = []
    for file in files:
        documents.append(
            document_service.upload(
                file,
                user_id=current_user.id,
                db=db,
                async_mode=async_mode,
                knowledge_base_name=knowledge_base_name,
                knowledge_base_category=knowledge_base_category,
                classification=classification,
                tags=[item.strip() for item in (tags or "").split(",") if item.strip()],
                permission_scope=permission_scope,
                sensitivity_level=sensitivity_level,
                permission_users=[item.strip() for item in (permission_users or "").split(",") if item.strip()],
                permission_roles=[item.strip() for item in (permission_roles or "").split(",") if item.strip()],
            )
        )
    return DocumentBatchUploadOut(documents=documents, count=len(documents))


@router.post("/qa-records/{qa_record_id}/feedback", response_model=DocumentQARecordOut)
def submit_document_qa_feedback(
    qa_record_id: int,
    req: DocumentQAFeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        record = document_qa_service.submit_feedback(
            qa_record_id=qa_record_id,
            user_id=current_user.id,
            feedback_value=req.feedback_value,
            feedback_reason=req.feedback_reason,
            feedback_note=req.feedback_note,
            db=db,
        )
        oplog_service.log(
            module="document",
            action="document_qa_feedback_submitted",
            db=db,
            user_id=current_user.id,
            target_type="document_qa_record",
            target_id=record.id,
            detail=f"value={record.feedback_value}; status={record.feedback_status}; document_id={record.document_id}",
        )
        return record
    except ValueError as e:
        detail = str(e)
        if detail == "QA record not found":
            raise api_error(404, "问答记录不存在", code="QA_RECORD_NOT_FOUND", detail=detail)
        raise api_error(400, "反馈内容不合法", code="DOCUMENT_QA_FEEDBACK_INVALID", detail=detail)
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "提交问答反馈失败", code="DOCUMENT_QA_FEEDBACK_FAILED", detail=str(e))


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = document_service.get(
        document_id,
        db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    if not doc:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")
    return doc


@router.patch("/{document_id}/download-policy", response_model=DocumentOut)
def update_document_download_policy(
    document_id: int,
    req: DocumentDownloadPolicyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")
    if not data_permission_service.can_modify_document(db, current_user, doc):
        raise api_error(403, "无权调整该文档的下载策略", code="DOCUMENT_DOWNLOAD_POLICY_FORBIDDEN")
    if req.download_enabled is None and req.watermark_required is None:
        raise api_error(400, "至少需要提供一项下载策略", code="DOCUMENT_DOWNLOAD_POLICY_INVALID")
    if req.download_enabled is not None:
        doc.download_enabled = req.download_enabled
    if req.watermark_required is not None:
        doc.watermark_required = req.watermark_required
    db.add(doc)
    db.commit()
    db.refresh(doc)
    oplog_service.log(
        module="document_security",
        action="document_download_policy_updated",
        db=db,
        user_id=current_user.id,
        target_type="document",
        target_id=doc.id,
        detail=f"download_enabled={doc.download_enabled}; watermark_required={doc.watermark_required}",
    )
    return doc


@router.get("/{document_id}/download")
def download_document(
    document_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = document_service.get(
        document_id,
        db,
        user_id=current_user.id,
        role=current_user.role,
        organization_id=current_user.organization_id,
        department_id=current_user.department_id,
    )
    if not doc:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")
    try:
        delivery = document_delivery_service.prepare_download(document=doc, user=current_user)
    except DocumentDeliveryError as exc:
        code = "DOCUMENT_DOWNLOAD_DISABLED" if "禁止下载" in str(exc) else "DOCUMENT_DOWNLOAD_UNAVAILABLE"
        raise api_error(403 if code == "DOCUMENT_DOWNLOAD_DISABLED" else 409, str(exc), code=code)

    oplog_service.log(
        module="document_security",
        action="document_downloaded",
        db=db,
        user_id=current_user.id,
        target_type="document",
        target_id=doc.id,
        detail=(
            f"content_hash={doc.content_hash or 'unavailable'}; "
            f"watermark_applied={delivery['watermark_applied']}; "
            f"watermark_supported={delivery['watermark_supported']}"
        ),
    )
    if delivery["temporary"]:
        background_tasks.add_task(document_delivery_service.cleanup, delivery["path"])
    return FileResponse(
        delivery["path"],
        filename=delivery["filename"],
        media_type=delivery["media_type"],
        background=background_tasks,
    )


class SummarizeRequest(BaseModel):
    max_length: int = 500
    async_mode: bool = False


class AnalyzeRequest(BaseModel):
    max_length: int = 500
    async_mode: bool = False


class CompareRequest(BaseModel):
    document_ids: list[int]
    max_length: int = 500


@router.post("/{document_id}/summarize")
async def summarize_document(document_id: int, req: SummarizeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        if req.async_mode:
            from app.tasks import summarize_document_task
            from app.services.authorization_service import authorization_service

            job = document_job_service.create_job(
                document_id=document_id,
                user_id=current_user.id,
                job_type="document_summary",
                db=db,
                current_step="submitted",
                message="文档摘要任务已提交",
            )
            # 长流程权限快照：保证后台执行期间权限范围稳定。
            ctx = authorization_service.build_context(db, current_user)
            snapshot_id = authorization_service.capture_snapshot(
                db, current_user, ctx, document_ids=[document_id],
            )
            task = summarize_document_task.delay(
                document_id, current_user.id, req.max_length, snapshot_id,
            )
            document_job_service.attach_task_id(job.id, task.id, db)
            oplog_service.log(
                module="async_task",
                action="document_summary_submitted",
                db=db,
                user_id=current_user.id,
                target_type="document",
                target_id=document_id,
                detail=f"task_id={task.id}; max_length={req.max_length}",
            )
            return {
                "document_id": document_id,
                "task_id": task.id,
                "state": "PENDING",
                "async_mode": True,
            }

        raw_text = document_service.summarize(
            document_id,
            db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        summary = await analysis_service.summarize_document(raw_text, max_length=req.max_length, user_id=current_user.id)
        doc = document_service.get(
            document_id,
            db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        if doc:
            doc.summary = summary
            db.commit()
        return {"document_id": document_id, "summary": summary}
    except ValueError as e:
        raise api_error(404, str(e), code="DOCUMENT_NOT_FOUND")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "文档摘要失败", code="DOCUMENT_SUMMARY_FAILED", detail=str(e))


@router.post("/{document_id}/analyze")
async def analyze_document(document_id: int, req: AnalyzeRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        if req.async_mode:
            from app.tasks import analyze_document_task
            from app.services.authorization_service import authorization_service

            job = document_job_service.create_job(
                document_id=document_id,
                user_id=current_user.id,
                job_type="document_analysis",
                db=db,
                current_step="submitted",
                message="文档分析任务已提交",
            )
            ctx = authorization_service.build_context(db, current_user)
            snapshot_id = authorization_service.capture_snapshot(
                db, current_user, ctx, document_ids=[document_id],
            )
            task = analyze_document_task.delay(
                document_id, current_user.id, req.max_length, snapshot_id,
            )
            document_job_service.attach_task_id(job.id, task.id, db)
            oplog_service.log(
                module="async_task",
                action="document_analysis_submitted",
                db=db,
                user_id=current_user.id,
                target_type="document",
                target_id=document_id,
                detail=f"task_id={task.id}; max_length={req.max_length}",
            )
            return {
                "document_id": document_id,
                "task_id": task.id,
                "state": "PENDING",
                "async_mode": True,
            }

        return await document_service.analyze(
            document_id=document_id,
            db=db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
            max_length=req.max_length,
        )
    except ValueError as e:
        raise api_error(404, str(e), code="DOCUMENT_NOT_FOUND")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "文档分析失败", code="DOCUMENT_ANALYSIS_FAILED", detail=str(e))


@router.post("/{document_id}/analyze-visual", response_model=DocumentVisualAnalyzeOut)
async def analyze_document_visual(
    document_id: int,
    req: DocumentVisualAnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return await document_service.analyze_visual(
            document_id=document_id,
            prompt=req.prompt,
            db=db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
    except ValueError as e:
        detail = str(e)
        if detail == "Document not found":
            raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND", detail=detail)
        raise api_error(400, "文档视觉分析请求不合法", code="DOCUMENT_VISUAL_ANALYSIS_INVALID", detail=detail)
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "文档视觉分析失败", code="DOCUMENT_VISUAL_ANALYSIS_FAILED", detail=str(e))


@router.post("/compare")
async def compare_documents(req: CompareRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return await document_service.compare(
            document_ids=req.document_ids,
            db=db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
            max_length=req.max_length,
        )
    except ValueError as e:
        raise api_error(400, str(e), code="DOCUMENT_COMPARE_INVALID")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "文档对比失败", code="DOCUMENT_COMPARE_FAILED", detail=str(e))


class AskRequest(BaseModel):
    question: str


@router.post("/{document_id}/ask")
def ask_document(document_id: int, req: AskRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return document_service.ask(
            document_id,
            req.question,
            db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
    except ValueError as e:
        raise api_error(404, str(e), code="DOCUMENT_NOT_FOUND")
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "文档问答失败", code="DOCUMENT_QA_FAILED", detail=str(e))


@router.post("/{document_id}/extract-risks")
async def extract_risks(document_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        risks = await document_service.extract_risks(
            document_id,
            db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        return {"document_id": document_id, "risks": risks}
    except ValueError as e:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND", detail=str(e))
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "文档风险提取失败", code="DOCUMENT_RISK_EXTRACT_FAILED", detail=str(e))


@router.post("/{document_id}/extract-todos")
async def extract_todos(document_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        todos = await document_service.extract_todos(
            document_id,
            db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        return {"document_id": document_id, "todos": todos}
    except ValueError as e:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND", detail=str(e))
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "文档待办提取失败", code="DOCUMENT_TODO_EXTRACT_FAILED", detail=str(e))


@router.post("/{document_id}/create-tasks")
async def create_tasks_from_document(document_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        tasks = await task_service.extract_from_document(document_id, current_user.id, db)
        return {
            "document_id": document_id,
            "created_tasks": len(tasks),
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "assignee": task.assignee,
                    "priority": task.priority,
                    "status": task.status,
                    "due_date": task.due_date,
                }
                for task in tasks
            ],
        }
    except ValueError as e:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND", detail=str(e))
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "从文档创建任务失败", code="DOCUMENT_CREATE_TASKS_FAILED", detail=str(e))


@router.post("/{document_id}/extract-clauses")
async def extract_key_clauses(document_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        clauses = await document_service.extract_key_clauses(
            document_id,
            db,
            user_id=current_user.id,
            role=current_user.role,
            organization_id=current_user.organization_id,
            department_id=current_user.department_id,
        )
        return {"document_id": document_id, "clauses": clauses}
    except ValueError as e:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND", detail=str(e))
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "关键条款提取失败", code="DOCUMENT_CLAUSE_EXTRACT_FAILED", detail=str(e))


@router.get("/task/{task_id}/status")
def get_task_status(task_id: str, current_user: User = Depends(get_current_user)):
    result = celery_app.AsyncResult(task_id)
    return serialize_async_result(result)


@router.get("/{document_id}/parse-jobs")
def list_document_parse_jobs(
    document_id: int,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models.document import DocumentParseJob

    doc = document_service.get(document_id, db, user_id=current_user.id, role=current_user.role, organization_id=current_user.organization_id, department_id=current_user.department_id)
    if not doc:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")
    query = db.query(DocumentParseJob).filter(
        DocumentParseJob.document_id == document_id,
        DocumentParseJob.user_id == current_user.id,
    )
    total = query.count()
    rows = (
        query.order_by(DocumentParseJob.created_at.desc(), DocumentParseJob.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [DocumentParseJobOut.model_validate(row).model_dump() for row in rows]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.post("/{document_id}/retry-parse")
def retry_document_parse(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.tasks import parse_document_task
    from app.services.authorization_service import authorization_service

    doc = document_service.get(document_id, db, user_id=current_user.id, role=current_user.role, organization_id=current_user.organization_id, department_id=current_user.department_id)
    if not doc:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")
    job = document_job_service.create_job(
        document_id=document_id,
        user_id=current_user.id,
        job_type="document_parse_retry",
        db=db,
        current_step="submitted",
        message="文档重试解析任务已提交",
    )
    ctx = authorization_service.build_context(db, current_user)
    snapshot_id = authorization_service.capture_snapshot(
        db, current_user, ctx, document_ids=[document_id],
    )
    task = parse_document_task.delay(doc.id, doc.version_number, doc.file_type, snapshot_id)
    document_job_service.attach_task_id(job.id, task.id, db)
    return {
        "document_id": document_id,
        "job_id": job.id,
        "task_id": task.id,
        "state": "PENDING",
    }


@router.get("/{document_id}/versions")
def list_document_versions(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = document_service.get(document_id, db, user_id=current_user.id, role=current_user.role, organization_id=current_user.organization_id, department_id=current_user.department_id)
    if not doc:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")
    root_id = doc.parent_document_id or doc.id
    from app.models.document import Document

    rows = (
        db.query(Document)
        .filter((Document.id == root_id) | (Document.parent_document_id == root_id))
        .order_by(Document.version_number.desc(), Document.id.desc())
        .all()
    )
    items = [
        {
            "id": row.id,
            "title": row.title,
            "version_number": row.version_number,
            "content_hash": row.content_hash,
            "status": row.status,
            "created_at": row.created_at,
        }
        for row in rows
        if document_service.get(row.id, db, user_id=current_user.id, role=current_user.role, organization_id=current_user.organization_id, department_id=current_user.department_id)
    ]
    return {"document_id": document_id, "items": items, "total": len(items)}


@router.get("/{document_id}/qa-records")
def list_document_qa_records(
    document_id: int,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models.document import DocumentQARecord

    doc = document_service.get(document_id, db, user_id=current_user.id, role=current_user.role, organization_id=current_user.organization_id, department_id=current_user.department_id)
    if not doc:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")
    query = db.query(DocumentQARecord).filter(
        DocumentQARecord.document_id == document_id,
        DocumentQARecord.user_id == current_user.id,
    )
    total = query.count()
    rows = (
        query.order_by(DocumentQARecord.created_at.desc(), DocumentQARecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [DocumentQARecordOut.model_validate(row).model_dump() for row in rows]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/{document_id}/qa-replays")
def list_document_qa_replays(
    document_id: int,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models.document import DocumentQARecord

    doc = document_service.get(document_id, db, user_id=current_user.id, role=current_user.role, organization_id=current_user.organization_id, department_id=current_user.department_id)
    if not doc:
        raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")
    query = db.query(DocumentQARecord).filter(
        DocumentQARecord.document_id == document_id,
        DocumentQARecord.user_id == current_user.id,
    )
    total = query.count()
    rows = (
        query.order_by(DocumentQARecord.created_at.desc(), DocumentQARecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [document_qa_service.serialize_record(row) for row in rows]
    return paginated_payload(items, total=total, page=page, page_size=page_size)
