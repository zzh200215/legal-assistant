import re
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings
from app.core.obs_context import (
    build_context,
    current_request_id,
    get_context,
    new_trace_id,
    set_context,
    valid_request_id,
)

# 需要记录日志的路径前缀和模块映射
LOG_RULES = [
    ("/api/documents", "document"),
    ("/api/tasks", "task"),
    ("/api/agent", "agent"),
    ("/api/chat", "chat"),
    ("/api/prompts", "prompt"),
    ("/api/auth/login", "auth"),
]

# 只记录写操作的方法
WRITE_METHODS = {"POST", "PUT", "DELETE"}

# 路由模板归一化：数字段 → {id}，避免 metrics 高基数标签。
_PATH_SEG_RE = re.compile(r"/[0-9]+(?=/|$)")


def route_template(path: str) -> str:
    """把具体路径归一化为路由模板（/api/documents/123 -> /api/documents/{id}）。"""
    normalized = _PATH_SEG_RE.sub("/{id}", path)
    # 长查询参数等不进入模板
    if len(normalized) > 120:
        normalized = normalized[:120]
    return normalized


class OperationLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # 1) 关联上下文：若外层 ObservabilityContextMiddleware 已构建（含外部 trace_id），
        #    只补缺不重建——重建会丢弃外部传入的 X-Trace-Id（P1 修复）。
        current = get_context()
        if current.request_id is None:
            header = request.headers.get(get_settings().OBS_REQUEST_ID_HEADER, "")
            request_id = valid_request_id(header) or new_trace_id()
            set_context(build_context(request_id=request_id))
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = int((time.perf_counter() - start) * 1000)
        response.headers.setdefault(get_settings().OBS_REQUEST_ID_HEADER, current_request_id())

        self._record_access(request, response, duration_ms)

        # 2) 操作日志（仅写操作，按路径前缀）。
        self._maybe_record_oplog(request, response)

        return response

    def _record_access(self, request: Request, response: Response, duration_ms: int) -> None:
        try:
            from app.core.observability import log_access

            client_ip = request.client.host if request.client else None
            route = route_template(request.url.path)
            log_access(
                method=request.method,
                route=route,
                status_code=response.status_code,
                duration_ms=duration_ms,
                client_ip=client_ip,
            )
            # P1：API p95 SLO 采集（进程内直方图，非阻塞；高基数标签已剔除）。
            from app.core.metrics import metrics

            status_class = f"{response.status_code // 100}xx"
            endpoint_group = "health" if request.url.path.startswith("/api/health") else "api"
            metrics.observe(
                "api_request_duration",
                duration_ms,
                labels={"route": route, "status_class": status_class, "endpoint_group": endpoint_group},
            )
        except Exception:
            pass  # access 日志/指标失败不影响请求

    def _maybe_record_oplog(self, request: Request, response: Response) -> None:
        path = request.url.path
        method = request.method
        module = None
        for prefix, mod in LOG_RULES:
            if path.startswith(prefix):
                module = mod
                break

        if module and method in WRITE_METHODS and response.status_code < 500:
            try:
                self._record_log(request, module, method, path)
            except Exception:
                pass  # 日志记录失败不影响请求

    def _record_log(self, request: Request, module: str, method: str, path: str):
        from app.core.database import SessionLocal
        from app.services.observability.oplog_service import oplog_service

        # 获取用户 ID（仅解码已认证 token，不信任外部头）。
        user_id = None
        try:
            from app.core.auth import decode_token
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                payload = decode_token(auth[7:])
                user_id = payload.get("sub") if payload else None
                if user_id:
                    user_id = int(str(user_id))
        except Exception:
            pass

        # 动作描述
        action_map = {
            "POST": "创建",
            "PUT": "更新",
            "DELETE": "删除",
        }
        action = action_map.get(method, method)

        # 从路径提取 target_type 和 target_id
        target_type = None
        target_id = None
        parts = path.rstrip("/").split("/")
        if len(parts) >= 5:
            # /api/documents/123/summarize -> target_type=document, target_id=123
            target_type = parts[3]
            try:
                target_id = int(parts[4])
            except ValueError:
                pass

        ip = request.client.host if request.client else None

        db = SessionLocal()
        try:
            oplog_service.log(
                module=module,
                action=f"{action} {path}",
                db=db,
                user_id=user_id,
                target_type=target_type,
                target_id=target_id,
                detail=f"{method} {path}",
                ip_address=ip,
            )
        finally:
            db.close()
