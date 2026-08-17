from __future__ import annotations

import json
import logging
from http import HTTPStatus
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


def success_payload(data: Any = None, message: str = "OK") -> dict[str, Any]:
    return {
        "success": True,
        "message": message,
        "data": data,
        "error": None,
    }


def api_error(status_code: int, message: str, *, code: str, detail: Any = None) -> HTTPException:
    payload = {
        "code": code,
        "message": message,
        "detail": detail if detail is not None else message,
    }
    return HTTPException(status_code=status_code, detail=payload)


def should_passthrough_exception(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) is not None


def error_payload(
    message: str,
    *,
    code: str,
    detail: Any = None,
    data: Any = None,
    field_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request_id, trace_id = _obs_ids()
    error = {
        "code": code,
        "detail": detail if detail is not None else message,
    }
    if field_errors:
        error["field_errors"] = field_errors
    return {
        "success": False,
        "message": message,
        "data": data,
        "error": error,
        "detail": message,
        "request_id": request_id,
        "trace_id": trace_id,
    }


def _obs_ids() -> tuple[str, str]:
    """从可观测上下文取 request_id/trace_id；无上下文时返回空串，绝不抛异常。"""
    try:
        from app.core.obs_context import current_request_id, current_trace_id
        return current_request_id() or "", current_trace_id() or ""
    except Exception:
        return "", ""


def paginated_payload(
    items: list[Any],
    *,
    total: int,
    page: int,
    page_size: int,
    message: str = "OK",
) -> dict[str, Any]:
    has_previous = page > 1
    has_next = page * page_size < total
    return success_payload(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_next": has_next,
            "has_previous": has_previous,
        },
        message=message,
    )


class ApiResponseMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        return await wrap_api_response(request, response)


async def wrap_api_response(request: Request, response: Response) -> Response:
    if not request.url.path.startswith("/api"):
        return response

    if response.headers.get("x-api-wrapped") == "1":
        return response

    media_type = getattr(response, "media_type", None) or response.headers.get("content-type", "")
    if "application/json" not in str(media_type).lower():
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    if not body:
        payload = success_payload()
    else:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return _rebuild_response(response, body)

    if isinstance(payload, dict) and {"success", "message", "data", "error"}.issubset(payload.keys()):
        wrapped = payload
        wrapped.setdefault("request_id", _obs_ids()[0])
        wrapped.setdefault("trace_id", _obs_ids()[1])
    else:
        message = HTTPStatus(response.status_code).phrase if response.status_code in HTTPStatus._value2member_map_ else "OK"
        request_id, trace_id = _obs_ids()
        wrapped = success_payload(payload, message=message)
        wrapped["request_id"] = request_id
        wrapped["trace_id"] = trace_id

    return JSONResponse(
        content=wrapped,
        status_code=response.status_code,
        headers=_filtered_headers(response, extra={"X-API-Version": "1"}),
    )


def _rebuild_response(response: Response, body: bytes) -> Response:
    return Response(
        content=body,
        status_code=response.status_code,
        headers=_filtered_headers(response),
        media_type=response.media_type,
    )


def _filtered_headers(response: Response, extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    if extra:
        headers.update(extra)
    return headers


async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    code = f"HTTP_{exc.status_code}"
    message = str(exc.detail) if exc.detail else HTTPStatus(exc.status_code).phrase
    detail = exc.detail

    if isinstance(exc.detail, dict):
        code = exc.detail.get("code") or code
        message = exc.detail.get("message") or exc.detail.get("detail") or message
        detail = exc.detail.get("detail", exc.detail)
    if exc.status_code >= 500:
        detail = message

    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(message, code=code, detail=detail),
        headers={"x-api-wrapped": "1"},
    )


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = []
    field_errors: list[dict[str, Any]] = []
    for item in exc.errors():
        normalized = dict(item)
        if "ctx" in normalized and normalized["ctx"] is not None:
            normalized["ctx"] = {key: str(value) for key, value in normalized["ctx"].items()}
        details.append(normalized)
        loc = normalized.get("loc") or []
        field_errors.append({
            "field": ".".join(str(part) for part in loc if part not in ("body", "query", "path", "header")),
            "code": str(normalized.get("type") or "INVALID"),
            "message": str(normalized.get("msg") or "参数校验失败"),
        })
    return JSONResponse(
        status_code=422,
        content=error_payload(
            "请求参数校验失败", code="VALIDATION_ERROR", detail=details, field_errors=field_errors,
        ),
        headers={"x-api-wrapped": "1"},
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    # 记录完整堆栈到服务端日志，避免 500 在生产静默不可见（Sentry 未配置时唯一线索）
    logger.exception("未处理异常: %s", exc)
    return JSONResponse(
        status_code=500,
        content=error_payload("服务器内部错误", code="INTERNAL_SERVER_ERROR", detail="服务器内部错误"),
        headers={"x-api-wrapped": "1"},
    )


async def stale_data_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """乐观锁冲突（version_id_col）：并发修改同一记录时返回 409。"""
    return JSONResponse(
        status_code=409,
        content=error_payload(
            "数据已被其他请求修改，请刷新后重试",
            code="CONCURRENT_UPDATE_CONFLICT",
            detail="并发更新冲突：记录已被他人修改，请重新加载后再操作",
        ),
        headers={"x-api-wrapped": "1"},
    )
