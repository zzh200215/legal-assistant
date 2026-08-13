"""统一通知模板服务（app/services/notification_template_service.py）。

- 按 ``channel + template_key + locale + version`` 精确选取；locale fallback：
  ``zh-CN -> zh -> default``，并记录实际使用版本。
- 版本不可原地覆盖：内容变化创建新版本，历史投递可追溯原模板（content_hash）。
- 渲染前校验参数 schema；禁止未校验的任意变量注入。
- 渲染输出做 HTML 转义，避免模板/参数注入。
"""

from __future__ import annotations

import hashlib
import json
import re
from html import escape

from sqlalchemy.orm import Session

from app.models.legal_notifications import NotificationTemplate

# 模板占位符：{{param}}
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# locale fallback 链：zh-CN -> zh -> default
_LOCALE_FALLBACK = ("zh-CN", "zh", "default")

_VALID_TYPES = ("string", "number", "integer", "boolean")


class TemplateValidationError(ValueError):
    """模板或参数不合法（渲染前校验失败）。"""


def _content_hash(channel: str, template_key: str, locale: str,
                  subject_template: str | None, body_template: str) -> str:
    canonical = "\x1f".join([channel, template_key, locale, subject_template or "", body_template])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class NotificationTemplateService:

    # ── 创建 / 版本管理 ──────────────────────────────────────────────

    def create_template(
        self,
        *,
        db: Session,
        channel: str,
        template_key: str,
        body_template: str,
        locale: str = "default",
        subject_template: str | None = None,
        params_schema: dict | None = None,
        status: str = "active",
        created_by: int | None = None,
        reviewed_by: int | None = None,
    ) -> NotificationTemplate:
        """创建模板；同 channel+key+locale 内容未变则复用最新版，内容变化则新建版本。"""
        digest = _content_hash(channel, template_key, locale, subject_template, body_template)
        latest = self._latest(db, channel, template_key, locale)
        if latest is not None and latest.content_hash == digest:
            return latest

        version = (latest.version if latest is not None else 0) + 1
        template = NotificationTemplate(
            channel=channel,
            template_key=template_key,
            locale=locale,
            version=version,
            subject_template=subject_template,
            body_template=body_template,
            params_schema_json=json.dumps(params_schema, ensure_ascii=False) if params_schema else None,
            status=status,
            created_by=created_by,
            reviewed_by=reviewed_by,
            content_hash=digest,
        )
        db.add(template)
        db.commit()
        db.refresh(template)
        return template

    def activate(self, *, db: Session, channel: str, template_key: str,
                 locale: str, version: int, reviewed_by: int | None = None) -> NotificationTemplate:
        """把指定版本置为 active（并记录 review）。"""
        template = (
            db.query(NotificationTemplate)
            .filter(
                NotificationTemplate.channel == channel,
                NotificationTemplate.template_key == template_key,
                NotificationTemplate.locale == locale,
                NotificationTemplate.version == version,
            )
            .first()
        )
        if not template:
            raise TemplateValidationError("模板版本不存在")
        template.status = "active"
        template.reviewed_by = reviewed_by or template.reviewed_by
        db.commit()
        db.refresh(template)
        return template

    # ── 选取 / 渲染 ─────────────────────────────────────────────────

    def resolve(self, *, db: Session, channel: str, template_key: str, locale: str) -> NotificationTemplate | None:
        """按 locale fallback 链选取最新 active 模板。

        fallback：精确 locale → 语言族（zh 系列 zh-CN→zh）→ default。
        未知 locale（如 en-US）无精确命中时直接回退 default，不误落 zh-CN。
        """
        candidates: list[str] = []
        if locale:
            candidates.append(locale)
        if locale and locale.lower().startswith("zh"):
            for cand in _LOCALE_FALLBACK[:2]:
                if cand not in candidates:
                    candidates.append(cand)
        if "default" not in candidates:
            candidates.append("default")
        for cand in candidates:
            latest = (
                db.query(NotificationTemplate)
                .filter(
                    NotificationTemplate.channel == channel,
                    NotificationTemplate.template_key == template_key,
                    NotificationTemplate.locale == cand,
                    NotificationTemplate.status == "active",
                )
                .order_by(NotificationTemplate.version.desc())
                .first()
            )
            if latest is not None:
                return latest
        return None

    def render(
        self,
        *,
        db: Session,
        channel: str,
        template_key: str,
        locale: str,
        params: dict | None = None,
        escape_html: bool = True,
    ) -> dict:
        """选取模板并渲染，返回 subject/body 与使用的模板版本信息。"""
        template = self.resolve(db=db, channel=channel, template_key=template_key, locale=locale)
        if template is None:
            raise TemplateValidationError(
                f"模板不存在: channel={channel} key={template_key} locale={locale}"
            )
        params = dict(params or {})
        self.validate_params(params, template.params_schema_json)

        values: dict[str, str] = {}
        for match in _PLACEHOLDER_RE.finditer(template.body_template):
            key = match.group(1)
            raw = params.get(key)
            if raw is None:
                raise TemplateValidationError(f"模板缺少必填参数: {key}")
            value = str(raw)
            values[key] = escape(value) if escape_html else value

        def _substitute(text: str) -> str:
            def _repl(match: "re.Match[str]") -> str:
                return values.get(match.group(1), match.group(0))
            return _PLACEHOLDER_RE.sub(_repl, text)

        subject = None
        if template.subject_template:
            for match in _PLACEHOLDER_RE.finditer(template.subject_template):
                key = match.group(1)
                if params.get(key) is None:
                    raise TemplateValidationError(f"主题缺少必填参数: {key}")
            subject = _substitute(template.subject_template)
        body = _substitute(template.body_template)

        return {
            "subject": subject,
            "body": body,
            "template_key": template.template_key,
            "template_version": template.version,
            "locale": template.locale,
            "content_hash": template.content_hash,
        }

    @staticmethod
    def validate_params(params: dict, params_schema_json: str | None) -> None:
        """按 JSON Schema 校验渲染参数；缺失/类型不符抛 TemplateValidationError。"""
        if not params_schema_json:
            return
        try:
            schema = json.loads(params_schema_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise TemplateValidationError("模板参数 schema 非法")
        if not isinstance(schema, dict):
            raise TemplateValidationError("模板参数 schema 非法")
        required = schema.get("required") or []
        properties = schema.get("properties") or {}
        for key in required:
            if key not in params:
                raise TemplateValidationError(f"缺少必填参数: {key}")
        for key, value in params.items():
            prop = properties.get(key)
            if not prop:
                continue
            expected = prop.get("type")
            if expected and expected in _VALID_TYPES:
                if expected == "string" and not isinstance(value, str):
                    raise TemplateValidationError(f"参数 {key} 应为字符串")
                if expected == "integer" and not isinstance(value, int):
                    raise TemplateValidationError(f"参数 {key} 应为整数")
                if expected == "number" and not isinstance(value, (int, float)):
                    raise TemplateValidationError(f"参数 {key} 应为数字")
                if expected == "boolean" and not isinstance(value, bool):
                    raise TemplateValidationError(f"参数 {key} 应为布尔值")

    @staticmethod
    def _latest(db: Session, channel: str, template_key: str, locale: str) -> NotificationTemplate | None:
        return (
            db.query(NotificationTemplate)
            .filter(
                NotificationTemplate.channel == channel,
                NotificationTemplate.template_key == template_key,
                NotificationTemplate.locale == locale,
            )
            .order_by(NotificationTemplate.version.desc())
            .first()
        )


notification_template_service = NotificationTemplateService()
