from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware


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
) -> dict[str, Any]:
    return {
        "success": False,
        "message": message,
        "data": data,
        "error": {
            "code": code,
            "detail": detail if detail is not None else message,
        },
        "detail": message,
    }


def paginated_payload(
    items: list[Any],
    *,
    total: int,
    page: int,
    page_size: int,
    message: str = "OK",
) -> dict[str, Any]:
    return success_payload(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
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
    else:
        message = HTTPStatus(response.status_code).phrase if response.status_code in HTTPStatus._value2member_map_ else "OK"
        wrapped = success_payload(payload, message=message)

    return JSONResponse(
        content=wrapped,
        status_code=response.status_code,
        headers=_filtered_headers(response),
    )


def _rebuild_response(response: Response, body: bytes) -> Response:
    return Response(
        content=body,
        status_code=response.status_code,
        headers=_filtered_headers(response),
        media_type=response.media_type,
    )


def _filtered_headers(response: Response) -> dict[str, str]:
    return {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }


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
    for item in exc.errors():
        normalized = dict(item)
        if "ctx" in normalized and normalized["ctx"] is not None:
            normalized["ctx"] = {key: str(value) for key, value in normalized["ctx"].items()}
        details.append(normalized)
    return JSONResponse(
        status_code=422,
        content=error_payload("请求参数校验失败", code="VALIDATION_ERROR", detail=details),
        headers={"x-api-wrapped": "1"},
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_payload("服务器内部错误", code="INTERNAL_SERVER_ERROR", detail="服务器内部错误"),
        headers={"x-api-wrapped": "1"},
    )
