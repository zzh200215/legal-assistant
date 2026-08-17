"""API 统一契约 DTO：Job 响应 / 错误 envelope / 分页（P1 API 统一化）。

所有 DTO 仅用于响应契约与 OpenAPI 文档生成；旧客户端兼容字段以
``task_id`` 别名保留（新增 ``job_id`` 后两者同值）。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    expired = "expired"


class JobError(BaseModel):
    code: str
    message: str


class JobOut(BaseModel):
    """统一异步任务响应：创建（202）与查询（200）共用。"""

    job_id: int
    # 兼容别名：既有 /v1/tasks/{id} 与旧客户端使用 task_id，两者同值。
    task_id: Optional[int] = None
    job_type: str
    status: JobStatus
    progress: Optional[int] = None
    status_url: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    result_summary: Optional[str] = None
    error: Optional[JobError] = None
    retry_count: Optional[int] = None
    estimated_completion: Optional[datetime] = None


class ErrorField(BaseModel):
    field: str
    code: str
    message: str


class ErrorDetail(BaseModel):
    code: str
    detail: Optional[Any] = None
    field_errors: Optional[list[ErrorField]] = None


class ErrorEnvelope(BaseModel):
    success: bool = False
    message: str
    data: Optional[Any] = None
    error: Optional[ErrorDetail] = None
    detail: Optional[str] = None
    request_id: str
    trace_id: str


class PagePayload(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int
    has_next: bool
    has_previous: bool


class SuccessEnvelope(BaseModel):
    success: bool = True
    message: str
    data: Optional[Any] = None
    error: None = None
    request_id: str
    trace_id: str


class IdempotencyKeyParam(BaseModel):
    """Idempotency-Key header 契约（写接口统一声明）。"""

    model_config = {"extra": "forbid"}

    value: Optional[str] = Field(
        default=None,
        description="幂等键：同 key + 同请求指纹重放原结果；同 key + 异指纹返回 409",
    )


class IfMatchParam(BaseModel):
    """If-Match header 契约（版本化更新接口统一声明）。"""

    model_config = {"extra": "forbid"}

    value: Optional[str] = Field(
        default=None,
        description='ETag 值（形如 "v{n}"）：不匹配返回 409 CONCURRENT_UPDATE_CONFLICT',
    )
