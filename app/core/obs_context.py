"""统一关联上下文（P1 可观测性）。

使用 contextvars 保存请求/任务级观察上下文，禁止模块级可变 dict：
- 不同请求/worker 任务之间上下文天然隔离，不互相泄漏。
- API 入口生成/校验 request_id，从已认证上下文注入 user_id/org_id；
  外部传入的关联 ID 仅按格式白名单校验，不信任外部身份字段。
- API -> Celery 通过受控 headers 传播；Celery 执行时恢复并向下游（LLM、
  Agent、文档、通知）继续携带。
- 字段缺失时使用显式 unknown/null，不伪造关联关系。

对外提供的 trace_id 采样：trace_id 缺失时按 OBS_CONTEXT_SAMPLE_RATE 决定
是否生成，保证日志/审计全量而 trace 字段可控。
"""

from __future__ import annotations

import contextvars
import re
import uuid
from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings

UNKNOWN = "unknown"

# 外部传入关联 ID 的格式白名单：字母/数字/下划线/连字符，8~64 位。
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")

# 受控 headers（API -> Celery -> 业务链路传播键）。
HDR_REQUEST_ID = "X-Obs-Request-Id"
HDR_TRACE_ID = "X-Obs-Trace-Id"
HDR_USER_ID = "X-Obs-User-Id"
HDR_ORG_ID = "X-Obs-Org-Id"


@dataclass(frozen=True)
class ObservabilityContext:
    """不可变观察上下文；缺失字段为 None（转字符串时用 unknown）。"""

    request_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    user_id: Optional[int] = None
    org_id: Optional[int] = None
    task_id: Optional[str] = None
    agent_run_id: Optional[int] = None
    workspace_id: Optional[int] = None
    service_name: str = "aibg-api"
    environment: str = "development"
    # 采样判定：trace 字段是否纳入本次链路。
    sampled: bool = True

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id or UNKNOWN,
            "trace_id": self.trace_id or UNKNOWN,
            "span_id": self.span_id or UNKNOWN,
            "user_id": self.user_id,
            "org_id": self.org_id,
            "task_id": self.task_id or UNKNOWN,
            "agent_run_id": self.agent_run_id,
            "workspace_id": self.workspace_id,
            "service": self.service_name,
            "environment": self.environment,
        }

    def as_headers(self) -> dict[str, str]:
        """序列化为受控 headers（API -> Celery）。内部 ID 仅取已认证/已生成值。"""
        headers: dict[str, str] = {}
        if self.request_id:
            headers[HDR_REQUEST_ID] = self.request_id
        if self.trace_id:
            headers[HDR_TRACE_ID] = self.trace_id
        if self.user_id is not None:
            headers[HDR_USER_ID] = str(self.user_id)
        if self.org_id is not None:
            headers[HDR_ORG_ID] = str(self.org_id)
        return headers


# 当前上下文（contextvars，隔离于请求/任务）。
_current_context: contextvars.ContextVar[ObservabilityContext] = contextvars.ContextVar(
    "obs_context", default=ObservabilityContext()
)


def get_context() -> ObservabilityContext:
    return _current_context.get()


def set_context(ctx: ObservabilityContext) -> None:
    _current_context.set(ctx)


def reset_context() -> None:
    _current_context.set(ObservabilityContext())


def with_context(ctx: ObservabilityContext):
    """上下文管理器：进入时设置 ctx，退出时恢复原值（嵌套安全）。"""
    import contextlib

    @contextlib.contextmanager
    def _manager():
        token = _current_context.set(ctx)
        try:
            yield ctx
        finally:
            _current_context.reset(token)

    return _manager()


def enrich_context(
    *,
    user_id: Optional[int] = None,
    org_id: Optional[int] = None,
    task_id: Optional[str] = None,
    agent_run_id: Optional[int] = None,
    workspace_id: Optional[int] = None,
) -> ObservabilityContext:
    """在已认证/已确定身份后补齐 user_id/org_id 等字段（身份必须来自可信来源）。

    仅覆盖显式传入的字段；其余保持当前值，返回新不可变上下文并设置。
    """
    current = get_context()
    ctx = ObservabilityContext(
        request_id=current.request_id,
        trace_id=current.trace_id,
        span_id=current.span_id,
        user_id=user_id if user_id is not None else current.user_id,
        org_id=org_id if org_id is not None else current.org_id,
        task_id=task_id if task_id is not None else current.task_id,
        agent_run_id=agent_run_id if agent_run_id is not None else current.agent_run_id,
        workspace_id=workspace_id if workspace_id is not None else current.workspace_id,
        service_name=current.service_name,
        environment=current.environment,
        sampled=current.sampled,
    )
    set_context(ctx)
    return ctx


# ── 便捷访问 ──────────────────────────────────────────────────────────

def current_request_id() -> str:
    return get_context().request_id or UNKNOWN


def current_trace_id() -> str:
    return get_context().trace_id or UNKNOWN


def current_user_id() -> Optional[int]:
    return get_context().user_id


def current_org_id() -> Optional[int]:
    return get_context().org_id


def current_task_id() -> str:
    return get_context().task_id or UNKNOWN


# ── 校验 / 生成 ────────────────────────────────────────────────────────

def valid_request_id(value: Optional[str]) -> Optional[str]:
    """按格式白名单校验外部传入 request_id/trace_id；非法返回 None。"""
    if not value:
        return None
    return value if _REQUEST_ID_RE.match(value) else None


def valid_trace_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value if _TRACE_ID_RE.match(value) else None


def new_request_id() -> str:
    return uuid.uuid4().hex[:32]


def new_trace_id() -> str:
    return uuid.uuid4().hex[:32]


def should_sample(request_id: Optional[str] = None) -> bool:
    """按配置采样率决定是否生成/继承 trace 字段。"""
    settings = get_settings()
    rate = float(settings.OBS_CONTEXT_SAMPLE_RATE)
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    # 简单确定性采样：按 request_id 哈希取模，避免同请求多次采样不一致。
    sample_key = request_id or get_context().request_id or new_request_id()
    bucket = int(_hashlib_hex(sample_key), 16) % 10000
    return bucket < int(rate * 10000)


def _hashlib_hex(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_context(
    *,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    user_id: Optional[int] = None,
    org_id: Optional[int] = None,
    task_id: Optional[str] = None,
    agent_run_id: Optional[int] = None,
    workspace_id: Optional[int] = None,
    sampled: Optional[bool] = None,
) -> ObservabilityContext:
    """构建观察上下文。request_id/trace_id 先按白名单校验；trace 缺失时按采样生成。"""
    settings = get_settings()
    rid = valid_request_id(request_id) or new_request_id()
    supplied_trace = valid_trace_id(trace_id)
    effective_sampled = (supplied_trace is not None or should_sample(rid)) if sampled is None else sampled
    trace = None
    if effective_sampled:
        trace = supplied_trace or new_trace_id()
    return ObservabilityContext(
        request_id=rid,
        trace_id=trace,
        user_id=user_id,
        org_id=org_id,
        task_id=task_id,
        agent_run_id=agent_run_id,
        workspace_id=workspace_id,
        service_name="aibg-api",
        environment=settings.ENVIRONMENT or "development",
        sampled=effective_sampled,
    )


def context_from_headers(headers: dict | None) -> dict:
    """从受控 headers 提取已通过白名单校验的关联 ID（供 Celery task.request.headers）。"""
    headers = headers or {}
    request_id = valid_request_id(headers.get(HDR_REQUEST_ID))
    trace_id = valid_trace_id(headers.get(HDR_TRACE_ID))
    try:
        user_id = int(headers[HDR_USER_ID]) if headers.get(HDR_USER_ID) else None
    except (TypeError, ValueError):
        user_id = None
    try:
        org_id = int(headers[HDR_ORG_ID]) if headers.get(HDR_ORG_ID) else None
    except (TypeError, ValueError):
        org_id = None
    return {
        "request_id": request_id,
        "trace_id": trace_id,
        "user_id": user_id,
        "org_id": org_id,
    }


def enqueue_headers() -> dict[str, str]:
    """API→Celery 入队时注入的受控 headers（P1）：把当前上下文关联 ID 随任务传播。

    所有 ``.delay()`` 调用点统一使用本函数，避免各自拼接；身份字段只取已认证值。
    """
    return get_context().as_headers()


# 兼容别名：db_monitor 等既有关联 id 可复用。
def correlation_id() -> str:
    ctx = get_context()
    return ctx.trace_id or ctx.request_id or UNKNOWN
