"""通知模板管理（管理员）：内置 HTML 版式的文案覆写、版本与预览。

设计取舍：
- 内置版式在代码里（见 ``app/services/notification/email_templates.py``），本接口只管理
  ``notification_templates`` 里的数据库覆写层；
- ``body_format=layout`` 只改 4 个文案字段（subject/lead/action_label/footnote），
  HTML 结构不动——这是「改文案不丢版式」的主路径；
- ``body_format=html`` 允许整段自定义 HTML，纯文本回退由服务端剥标签生成；
- 新建一律是 draft，必须显式启用；启用时同 key+locale 的其它 active 版本置为 retired。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.core.auth import require_admin_user
from app.core.database import get_db
from app.models.legal_notifications import NotificationTemplate
from app.models.user import User
from app.services.notification.email_templates import (
    LAYOUT_FIELD_LIMITS, LAYOUT_OVERRIDE_FIELDS, LayoutOverrideError, action_url,
    describe_builtin_templates, html_to_text, parse_layout_overrides, render_builtin_email,
)
from app.services.notification.notification_template_service import (
    TemplateValidationError, notification_template_service,
)
from app.services.observability.oplog_service import oplog_service

router = APIRouter()

PREVIEW_PARAMS = {
    "title": "审核即将到期：股权转让协议（剩 6 小时）",
    "body": "合同审查 #12 的审核截止时间 2026-10-27 09:00\n提交人：王律师 · 关联案件：星河科技股权转让",
    "reference_id": "12",
}
PREVIEW_REFERENCE_TYPE = "contract_review"


class TemplateDraftIn(BaseModel):
    template_key: str
    locale: str = "zh-CN"
    body_format: str = "layout"
    subject_template: str | None = None
    body_template: str | None = None
    layout: dict[str, str] | None = None
    params_schema: dict | None = None


class TemplatePreviewIn(BaseModel):
    template_key: str
    locale: str = "zh-CN"
    body_format: str = "layout"
    subject_template: str | None = None
    body_template: str | None = None
    layout: dict[str, str] | None = None
    params: dict[str, str] | None = None


def _serialize(template: NotificationTemplate) -> dict:
    return {
        "id": template.id,
        "template_key": template.template_key,
        "locale": template.locale,
        "version": template.version,
        "body_format": template.body_format or "text",
        "subject_template": template.subject_template,
        "body_template": template.body_template,
        "content_hash": template.content_hash,
        "status": template.status,
        "created_by": template.created_by,
        "reviewed_by": template.reviewed_by,
        "created_at": template.created_at.isoformat() if template.created_at else None,
        "updated_at": template.updated_at.isoformat() if template.updated_at else None,
    }


def _layout_json(layout: dict[str, str] | None) -> str:
    """校验并序列化布局覆写；只认白名单字段，避免把任意 JSON 写进模板表。"""
    payload = {key: value for key, value in (layout or {}).items() if value is not None}
    unknown = set(payload) - set(LAYOUT_OVERRIDE_FIELDS)
    if unknown:
        raise LayoutOverrideError(f"不支持的覆写字段: {', '.join(sorted(unknown))}")
    text = json.dumps(payload, ensure_ascii=False)
    parse_layout_overrides(text)  # 长度/类型校验复用同一套规则
    return text


def _build_body(body_format: str, body_template: str | None, layout: dict[str, str] | None) -> str:
    if body_format == "layout":
        return _layout_json(layout)
    if not (body_template or "").strip():
        raise TemplateValidationError("纯文本/HTML 模板必须提供正文")
    return body_template or ""


@router.get("/")
def list_notification_templates(
    template_key: str | None = Query(None, description="按模板键过滤"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    """内置模板清单 + 数据库覆写版本（默认按模板键/版本倒序）。"""
    query = db.query(NotificationTemplate).filter(NotificationTemplate.channel == "email")
    if template_key:
        query = query.filter(NotificationTemplate.template_key == template_key)
    rows = query.order_by(
        NotificationTemplate.template_key.asc(),
        NotificationTemplate.locale.asc(),
        NotificationTemplate.version.desc(),
    ).limit(200).all()

    active = {}
    for row in rows:
        if row.status == "active":
            active.setdefault(f"{row.template_key}:{row.locale}", _serialize(row))

    return {
        "builtin": describe_builtin_templates(),
        "templates": [_serialize(row) for row in rows],
        "active_overrides": list(active.values()),
        "layout_fields": [
            {"field": field, "max_length": LAYOUT_FIELD_LIMITS[field]}
            for field in LAYOUT_OVERRIDE_FIELDS
        ],
        "action_url_configured": action_url(PREVIEW_REFERENCE_TYPE) is not None,
    }


@router.post("/preview")
def preview_notification_template(
    req: TemplatePreviewIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    """渲染预览（不落库）：layout 走内置版式，html/text 用传入正文。"""
    body_format = notification_template_service.validate_body_format(req.body_format)
    params = {**PREVIEW_PARAMS, **(req.params or {})}
    href = action_url(PREVIEW_REFERENCE_TYPE)

    try:
        if body_format == "layout":
            overrides = parse_layout_overrides(_layout_json(req.layout))
            if not overrides.get("subject") and req.subject_template:
                overrides["subject"] = req.subject_template
            builtin = render_builtin_email(req.template_key, params={**params, "action_url": href},
                                           layout_overrides=overrides)
            if builtin is None:
                raise TemplateValidationError("该模板键没有内置版式，无法使用 layout 覆写")
            return {
                "body_format": body_format,
                "subject": builtin["subject"],
                "text": builtin["text"],
                "html": builtin["html"],
                "action_url_configured": href is not None,
            }

        body = _build_body(body_format, req.body_template, None)
        draft = NotificationTemplate(
            channel="email", template_key=req.template_key, locale=req.locale, version=0,
            subject_template=req.subject_template, body_template=body,
            body_format=body_format, content_hash="preview",
        )
        rendered = notification_template_service.render_instance(draft, params=params)
        html_body = rendered["body"] if body_format == "html" else None
        return {
            "body_format": body_format,
            "subject": rendered["subject"] or params["title"],
            "text": html_to_text(html_body) if body_format == "html" else rendered["body"],
            "html": html_body,
            "action_url_configured": href is not None,
        }
    except (TemplateValidationError, LayoutOverrideError) as exc:
        raise api_error(400, "模板内容不合法", code="NOTIFICATION_TEMPLATE_INVALID", detail=str(exc))


@router.post("/")
def create_notification_template(
    req: TemplateDraftIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    """新建模板草稿（同内容复用已有版本；不自动启用）。"""
    try:
        body_format = notification_template_service.validate_body_format(req.body_format)
        body = _build_body(body_format, req.body_template, req.layout)
        template = notification_template_service.create_template(
            db=db, channel="email", template_key=req.template_key.strip(),
            body_template=body, locale=req.locale or "zh-CN",
            subject_template=(req.subject_template or "").strip() or None,
            body_format=body_format, params_schema=req.params_schema,
            status="draft", created_by=current_user.id,
        )
    except (TemplateValidationError, LayoutOverrideError) as exc:
        raise api_error(400, "模板内容不合法", code="NOTIFICATION_TEMPLATE_INVALID", detail=str(exc))

    oplog_service.log(module="notification", action="notification_template_draft_saved", db=db,
                      user_id=current_user.id, target_type="notification_template",
                      target_id=template.id,
                      detail=f"key={template.template_key}; format={template.body_format}; version={template.version}")
    return _serialize(template)


@router.post("/{template_id}/activate")
def activate_notification_template(
    template_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    """启用指定版本；同 key+locale 的其它 active 版本转为 retired。"""
    template = db.query(NotificationTemplate).filter(NotificationTemplate.id == template_id).first()
    if template is None:
        raise api_error(404, "模板版本不存在", code="NOTIFICATION_TEMPLATE_NOT_FOUND")
    if template.status == "active":
        return _serialize(template)

    from app.services.notification.notification_template_service import notification_template_service as service

    try:
        service.activate(db=db, channel=template.channel, template_key=template.template_key,
                         locale=template.locale, version=template.version,
                         reviewed_by=current_user.id)
    except TemplateValidationError as exc:
        raise api_error(404, "模板版本不存在", code="NOTIFICATION_TEMPLATE_NOT_FOUND", detail=str(exc))
    db.refresh(template)
    oplog_service.log(module="notification", action="notification_template_activated", db=db,
                      user_id=current_user.id, target_type="notification_template",
                      target_id=template.id,
                      detail=f"key={template.template_key}; format={template.body_format}; version={template.version}")
    return _serialize(template)
