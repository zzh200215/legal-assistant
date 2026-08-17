"""Phase 13/14 — 开放平台 / 安全审计 / 异步任务 / 通知偏好 API（门面）。

原单文件按「开发者应用 / Open API / 异步任务 / 审计 / 通知·Onboarding」拆为五个子路由，
本模块只做聚合与 ``include_router``，对外 ``router`` / ``open_router`` 接口不变。
"""
from fastapi import APIRouter

from app.api.developer.async_jobs import router as _async_jobs_router
from app.api.developer.audit_operations import router as _audit_operations_router
from app.api.developer.developer_apps import _public_developer_app  # noqa: F401 - 重新导出供测试导入
from app.api.developer.developer_apps import router as _developer_apps_router
from app.api.developer.notifications_onboarding import router as _notifications_onboarding_router
from app.api.developer.open_review import _open_review_job_key  # noqa: F401 - 重新导出供测试导入
from app.api.developer.open_review import open_router as _open_review_router

router = APIRouter()
router.include_router(_developer_apps_router)
router.include_router(_async_jobs_router)
router.include_router(_audit_operations_router)
router.include_router(_notifications_onboarding_router)

open_router = APIRouter()
open_router.include_router(_open_review_router)
