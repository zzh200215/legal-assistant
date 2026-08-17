"""供应商无关的任务策略与统一失败分类。

- ``TaskPolicy``：按任务（chat/embedding/vision/rerank/...）定义 timeout、重试次数、
  fallback、temperature、max_tokens、预算/限流类别；旧 action 归一化到默认策略。
- ``ModelRequest``：一次模型请求的供应商无关描述（含 trace_id/request_id）。
- ``ModelError`` / ``ModelErrorKind`` / ``classify_error``：统一失败分类，
  决定错误是否属于明确瞬态错误（可重试/可 fallback）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import Enum

import httpx

from app.core.config import get_settings

settings = get_settings()

DEFAULT_TIMEOUT_SECONDS = 60.0
VISION_TIMEOUT_SECONDS = 120.0
EMBED_TIMEOUT_SECONDS = 60.0


class ModelErrorKind(str, Enum):
    VALIDATION = "validation"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    PROVIDER_5XX = "provider_5xx"
    CONTENT_BLOCKED = "content_blocked"
    INVALID_RESPONSE = "invalid_response"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    REPAIR_FAILED = "repair_failed"
    CIRCUIT_OPEN = "circuit_open"
    UNKNOWN = "unknown"


# 仅明确瞬态错误可重试/fallback；参数、鉴权、权限、内容拦截等不重试。
_RETRYABLE_KINDS = frozenset(
    {
        ModelErrorKind.TIMEOUT,
        ModelErrorKind.TRANSPORT,
        ModelErrorKind.PROVIDER_5XX,
        ModelErrorKind.RATE_LIMITED,
    }
)


class ModelError(Exception):
    """统一模型失败：kind 为稳定分类，retryable 指示是否瞬态。"""

    def __init__(
        self,
        *,
        kind: ModelErrorKind | str,
        message: str,
        status_code: int | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
    ):
        self.kind = kind if isinstance(kind, ModelErrorKind) else ModelErrorKind(kind)
        self.status_code = status_code
        self.request_id = request_id
        self.trace_id = trace_id
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        return self.kind in _RETRYABLE_KINDS


@dataclass(frozen=True)
class TaskPolicy:
    """按任务定义的请求参数策略；None 字段回退到全局设置（兼容旧行为）。"""

    task: str
    timeout_seconds: float | None = None
    max_retries: int | None = None
    fallback_enabled: bool = True
    fallback_max_retries: int | None = None
    fallback_target: str | None = None
    model_tier: str = "auto"  # "auto" 按文本/action 路由；"primary" 强制主模型
    temperature: float | None = None
    max_tokens: int | None = None
    budget_category: str = "text"
    rate_limit_category: str = "text"
    # 结构化输出修复：受 TaskPolicy 控制；max_attempts 有界，保证不会无限重试。
    structured_repair_enabled: bool = True
    structured_repair_max_attempts: int = 1


@dataclass(frozen=True)
class ModelRequest:
    """一次模型请求的供应商无关描述。"""

    request_type: str  # chat | generate | chat_stream | vision | embedding
    messages: list[dict] | None = None
    prompt: str | None = None
    image_urls: list[str] | None = None
    texts: list[str] | None = None
    temperature: float | None = None
    action: str = ""
    user_id: int | None = None
    prompt_template: str | None = None
    prompt_version: int | None = None
    # 请求前统一 token 估算（治理层 enforce_* 计算），随请求记录用于对比实际 usage。
    estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None
    trace_id: str = ""
    request_id: str = ""
    # P0 出站数据保护审计字段（由 llm_outbound_gate 填充；None/0 表示未检测或无命中）。
    # pii_hit_codes 为 JSON 数组字符串（仅规则 code，不含任何原始文本）。
    data_level: str | None = None
    pii_hit_codes: str | None = None
    pii_hit_count: int = 0
    redacted_count: int = 0


_TASK_POLICIES = {
    "chat": TaskPolicy(
        task="chat",
        temperature=0.7,
        budget_category="text",
        rate_limit_category="chat",
    ),
    "embedding": TaskPolicy(
        task="embedding",
        timeout_seconds=EMBED_TIMEOUT_SECONDS,
        model_tier="primary",
        budget_category="embedding",
        rate_limit_category="embedding",
    ),
    "vision": TaskPolicy(
        task="vision",
        timeout_seconds=VISION_TIMEOUT_SECONDS,
        model_tier="primary",
        budget_category="vision",
        rate_limit_category="vision",
    ),
    "rerank": TaskPolicy(
        task="rerank",
        temperature=0.0,
        budget_category="rerank",
        rate_limit_category="rerank",
    ),
}

_ACTION_TASK_OVERRIDES = {
    "embedding": "embedding",
    "generate_with_images": "vision",
    "rerank": "rerank",
    "rag_rerank": "rerank",
}


def _task_for_action(action: str) -> str:
    normalized = (action or "").lower()
    if normalized in _ACTION_TASK_OVERRIDES:
        return _ACTION_TASK_OVERRIDES[normalized]
    if "embed" in normalized:
        return "embedding"
    if "rerank" in normalized:
        return "rerank"
    if "image" in normalized or "vision" in normalized:
        return "vision"
    return "chat"


def get_task_policy(action: str) -> TaskPolicy:
    """返回 action 对应的任务策略；未知/旧 action 归一化到默认 chat 策略。"""
    return _TASK_POLICIES[_task_for_action(action)]


_CONTENT_BLOCK_MARKERS = ("content_filter", "content_policy", "moderation", "审核未通过", "内容违规", "敏感词")


def _looks_like_content_blocked(status_code: int, body: str) -> bool:
    if status_code not in (400, 403, 451):
        return False
    lowered = body.lower()
    return any(marker in lowered for marker in _CONTENT_BLOCK_MARKERS)


def _classify_http_status_error(exc: httpx.HTTPStatusError) -> ModelError:
    status = exc.response.status_code if exc.response is not None else 0
    body = ""
    if exc.response is not None:
        try:
            body = exc.response.text or ""
        except Exception:
            body = ""
    if _looks_like_content_blocked(status, body):
        return ModelError(kind=ModelErrorKind.CONTENT_BLOCKED, message=str(exc), status_code=status)
    if status == 400:
        return ModelError(kind=ModelErrorKind.VALIDATION, message=str(exc), status_code=status)
    if status == 401:
        return ModelError(kind=ModelErrorKind.AUTHENTICATION, message=str(exc), status_code=status)
    if status == 403:
        return ModelError(kind=ModelErrorKind.PERMISSION, message=str(exc), status_code=status)
    if status == 429:
        return ModelError(kind=ModelErrorKind.RATE_LIMITED, message=str(exc), status_code=status)
    if status >= 500:
        return ModelError(kind=ModelErrorKind.PROVIDER_5XX, message=str(exc), status_code=status)
    return ModelError(kind=ModelErrorKind.VALIDATION, message=str(exc), status_code=status)


def classify_error(exc: Exception) -> ModelError:
    """把任意异常归一为 ModelError（稳定分类 + 是否可重试）。"""
    if isinstance(exc, ModelError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http_status_error(exc)
    if isinstance(exc, httpx.ReadTimeout):
        return ModelError(kind=ModelErrorKind.TIMEOUT, message=str(exc))
    if isinstance(exc, (httpx.ConnectError, httpx.ProxyError, httpx.RemoteProtocolError)):
        return ModelError(kind=ModelErrorKind.TRANSPORT, message=str(exc))
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return ModelError(kind=ModelErrorKind.INVALID_RESPONSE, message=str(exc))
    return ModelError(kind=ModelErrorKind.UNKNOWN, message=str(exc))


def new_trace_id() -> str:
    return str(uuid.uuid4())
