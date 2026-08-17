"""Phase 11 — 关键日期 / 门户链接 / 案件成员 / 进度更新 API（门面）。

原单文件按「品牌/截止日 / 门户链接·OTP / 成员·进度」拆为三个子路由，
本模块只做聚合与 ``include_router``，对外 ``router`` 与 ``_SESSION_TTL`` 接口不变。
"""
from fastapi import APIRouter

from app.api.legal._portal_helpers import _SESSION_TTL  # noqa: F401 - 重新导出，供测试导入
from app.api.legal.case_members_progress import router as _case_members_progress_router
from app.api.legal.portal_branding_deadlines import router as _branding_deadlines_router
from app.api.legal.portal_links_access import router as _links_access_router

router = APIRouter()
router.include_router(_branding_deadlines_router)
router.include_router(_links_access_router)
router.include_router(_case_members_progress_router)
