from pydantic import BaseModel, ConfigDict
from datetime import datetime


class DocumentBase(BaseModel):
    title: str
    file_type: str


class DocumentCreate(DocumentBase):
    pass


class DocumentOut(DocumentBase):
    id: int
    user_id: int
    # 新文档只存 object_key（file_path 为存量本地路径，可为空）
    file_path: str | None = None
    object_key: str | None = None
    organization_id: int | None = None
    department_id: int | None = None
    knowledge_base_id: int | None = None
    parent_document_id: int | None = None
    version_number: int = 1
    content_hash: str | None = None
    classification: str | None = None
    tags: str | None = None
    permission_scope: str = "private"
    sensitivity_level: str = "internal"
    permission_users: str | None = None
    permission_roles: str | None = None
    metadata_json: str | None = None
    download_enabled: bool = True
    watermark_required: bool = False
    status: str
    summary: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentChunkOut(BaseModel):
    id: int
    document_id: int
    chunk_index: int
    content: str
    page_number: int | None = None
    section_title: str | None = None
    section_path: str | None = None
    segment_type: str | None = None
    table_like: bool = False
    visual_tags: str | None = None
    ocr_quality: float | None = None
    embedding_id: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentParseJobOut(BaseModel):
    id: int
    document_id: int
    user_id: int
    job_type: str
    task_id: str | None = None
    status: str
    progress: int | None = None
    current_step: str | None = None
    message: str | None = None
    error_message: str | None = None
    result_summary: str | None = None
    retry_count: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentQARecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
    id: int
    document_id: int
    user_id: int
    session_id: int | None = None
    question: str
    answer: str
    citations: str | None = None
    hit_chunks: str | None = None
    model_name: str | None = None
    latency_ms: int | None = None
    source: str
    feedback_value: str | None = None
    feedback_reason: str | None = None
    feedback_note: str | None = None
    feedback_status: str | None = None
    feedback_created_at: datetime | None = None
    feedback_resolved_at: datetime | None = None
    feedback_resolution_note: str | None = None
    feedback_resolved_by: int | None = None
    created_at: datetime


class DocumentVisualAnalyzeRequest(BaseModel):
    prompt: str = "请结合图片内容提取关键视觉信息，并尽量指出签字、公章、附件、日期、金额等要点。"


class DocumentVisualAnalyzeOut(BaseModel):
    document_id: int
    title: str
    file_type: str
    analysis: str
    image_count: int = 1


class KnowledgeBaseOut(BaseModel):
    id: int
    user_id: int
    organization_id: int | None = None
    department_id: int | None = None
    name: str
    description: str | None = None
    category: str | None = None
    permission_scope: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
