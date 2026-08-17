"""HTTP lifecycle integration for request-scoped observability context."""

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.obs_context import build_context, with_context


class ObservabilityContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # noqa: ANN001
        settings = get_settings()
        context = build_context(
            request_id=request.headers.get(settings.OBS_REQUEST_ID_HEADER),
            trace_id=request.headers.get("X-Trace-Id"),
        )
        with with_context(context):
            response = await call_next(request)
        response.headers.setdefault(settings.OBS_REQUEST_ID_HEADER, context.request_id)
        return response
