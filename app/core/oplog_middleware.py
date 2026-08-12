import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


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


class OperationLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # 判断是否需要记录
        path = request.url.path
        method = request.method
        module = None
        for prefix, mod in LOG_RULES:
            if path.startswith(prefix):
                module = mod
                break

        if module and method in WRITE_METHODS:
            try:
                self._record_log(request, module, method, path)
            except Exception:
                pass  # 日志记录失败不影响请求

        return response

    def _record_log(self, request: Request, module: str, method: str, path: str):
        from app.core.database import SessionLocal
        from app.services.oplog_service import oplog_service

        # 获取用户 ID
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
