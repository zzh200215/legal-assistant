"""通知模板测试：版本不可覆盖、locale fallback、参数校验、HTML 转义渲染。"""
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal_notifications import NotificationTemplate
from app.services.notification.notification_template_service import (
    TemplateValidationError, notification_template_service,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class NotificationTemplateTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_version_bumps_on_content_change_not_overwritten(self):
        v1 = notification_template_service.create_template(
            db=self.db, channel="email", template_key="deadline",
            body_template="案件 {{title}} 将在 {{date}} 到期", locale="zh-CN",
            subject_template="到期提醒：{{title}}", params_schema={"type": "object", "required": ["title", "date"]},
        )
        self.assertEqual(v1.version, 1)
        # 同内容 → 复用最新版
        same = notification_template_service.create_template(
            db=self.db, channel="email", template_key="deadline",
            body_template="案件 {{title}} 将在 {{date}} 到期", locale="zh-CN",
            subject_template="到期提醒：{{title}}", params_schema={"type": "object", "required": ["title", "date"]},
        )
        self.assertEqual(same.version, 1)
        self.assertEqual(same.id, v1.id)
        # 内容变化 → 新版本，历史版本保留
        v2 = notification_template_service.create_template(
            db=self.db, channel="email", template_key="deadline",
            body_template="案件 {{title}} 将于 {{date}} 到期，请关注", locale="zh-CN",
            subject_template="到期提醒：{{title}}", params_schema={"type": "object", "required": ["title", "date"]},
        )
        self.assertEqual(v2.version, 2)
        self.assertNotEqual(v2.id, v1.id)
        versions = self.db.query(NotificationTemplate).filter(
            NotificationTemplate.template_key == "deadline").count()
        self.assertEqual(versions, 2)

    def test_locale_fallback_zh_to_default(self):
        notification_template_service.create_template(
            db=self.db, channel="email", template_key="welcome", locale="zh-CN",
            body_template="中文模板 {{name}}", subject_template="欢迎 {{name}}",
            params_schema={"type": "object", "required": ["name"]},
        )
        notification_template_service.create_template(
            db=self.db, channel="email", template_key="welcome", locale="default",
            body_template="Default template {{name}}", subject_template="Welcome {{name}}",
            params_schema={"type": "object", "required": ["name"]},
        )
        # zh 请求 → 命中 zh-CN
        rendered = notification_template_service.render(
            db=self.db, channel="email", template_key="welcome", locale="zh",
            params={"name": "张三"},
        )
        self.assertEqual(rendered["locale"], "zh-CN")
        self.assertIn("中文模板", rendered["body"])
        # 未知 locale → fallback default
        rendered_en = notification_template_service.render(
            db=self.db, channel="email", template_key="welcome", locale="en-US",
            params={"name": "Alice"},
        )
        self.assertEqual(rendered_en["locale"], "default")
        self.assertIn("Default template", rendered_en["body"])

    def test_param_validation_rejects_missing_or_wrong_type(self):
        notification_template_service.create_template(
            db=self.db, channel="email", template_key="sign",
            body_template="合同 {{contract_no}} 已签署", locale="default",
            subject_template="签署完成", params_schema={
                "type": "object", "required": ["contract_no"],
                "properties": {"contract_no": {"type": "string"}},
            },
        )
        with self.assertRaises(TemplateValidationError):
            notification_template_service.render(
                db=self.db, channel="email", template_key="sign", locale="default", params={})
        with self.assertRaises(TemplateValidationError):
            notification_template_service.render(
                db=self.db, channel="email", template_key="sign", locale="default",
                params={"contract_no": 123})
        ok = notification_template_service.render(
            db=self.db, channel="email", template_key="sign", locale="default",
            params={"contract_no": "HT-2026-001"})
        self.assertEqual(ok["subject"], "签署完成")

    def test_render_escapes_html(self):
        notification_template_service.create_template(
            db=self.db, channel="email", template_key="notify",
            body_template="通知：{{content}}", locale="default",
            params_schema={"type": "object", "required": ["content"]},
        )
        rendered = notification_template_service.render(
            db=self.db, channel="email", template_key="notify", locale="default",
            params={"content": '<script>alert("x")</script> & 文本'},
        )
        self.assertNotIn("<script>", rendered["body"])
        self.assertIn("&lt;script&gt;", rendered["body"])
        self.assertIn("&amp;", rendered["body"])

    def test_render_records_template_version_and_hash(self):
        tpl = notification_template_service.create_template(
            db=self.db, channel="email", template_key="vtest",
            body_template="版本 {{v}}", locale="default",
            subject_template="V{{v}}", params_schema={"type": "object", "required": ["v"]},
        )
        rendered = notification_template_service.render(
            db=self.db, channel="email", template_key="vtest", locale="default", params={"v": "1"})
        self.assertEqual(rendered["template_version"], tpl.version)
        self.assertEqual(rendered["content_hash"], tpl.content_hash)


if __name__ == "__main__":
    unittest.main()


class EmailRenderFallbackTests(unittest.TestCase):
    """邮件渲染优先级：数据库模板 → 内置 HTML 版式 → 事件标题/正文。"""

    def _db(self):
        engine = _engine()
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        self.addCleanup(db.close)
        return db

    def _event(self, **kw):
        from app.models.legal_notifications import LegalNotificationEvent

        params = dict(
            organization_id=1, user_id=1, event_type="approval",
            title="审核即将到期：合同 A（剩 6 小时）", body="sla:due_soon · 合同 #1",
            channel="email", reference_type="contract_review", reference_id=12,
        )
        params.update(kw)
        return LegalNotificationEvent(**params)

    def test_unknown_template_key_falls_back_to_event_copy(self):
        from app.services.notification.notification_service import notification_service

        db = self._db()
        event = self._event(template_key="no_such_template_key")
        subject, body, html = notification_service._render_email_content(db, event)
        self.assertEqual(subject, event.title)
        self.assertEqual(body, event.body)
        self.assertIsNone(html)

    def test_builtin_template_renders_html_alternative(self):
        from app.services.notification.notification_service import notification_service

        db = self._db()
        event = self._event(template_key="review_sla_due_soon")
        subject, body, html = notification_service._render_email_content(db, event)
        self.assertEqual(subject, "【律智检】审核即将到期：合同 A（剩 6 小时）")
        self.assertIn("审核即将到期", body)
        self.assertIn("合同 #1", body)
        self.assertIsNotNone(html)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("合同 A", html)

    def test_builtin_action_url_omitted_without_base_url(self):
        from app.services.notification.email_templates import render_builtin_email
        from app.core.config import get_settings

        with patch.object(get_settings(), "NOTIFICATION_EMAIL_ACTION_BASE_URL", ""):
            rendered = render_builtin_email("review_sla_overdue", params={"title": "t", "body": "b"})
        self.assertNotIn("<a href=", rendered["html"])
        self.assertNotIn("http", rendered["text"])

    def test_builtin_action_url_uses_configured_base(self):
        from app.services.notification.email_templates import action_url, render_builtin_email
        from app.core.config import get_settings

        with patch.object(get_settings(), "NOTIFICATION_EMAIL_ACTION_BASE_URL", "https://legal.example.com/"):
            href = action_url("contract_review")
            self.assertEqual(href, "https://legal.example.com/legal-workspace?view=review")
            rendered = render_builtin_email("review_sla_due_soon",
                                            params={"title": "t", "body": "b", "action_url": href})
        self.assertIn('href="https://legal.example.com/legal-workspace?view=review"', rendered["html"])
        self.assertIn(href, rendered["text"])

    def test_database_template_takes_precedence_over_builtin(self):
        from app.services.notification.notification_service import notification_service

        db = self._db()
        notification_template_service.create_template(
            db=db, channel="email", template_key="review_sla_due_soon",
            subject_template="到期：{{title}}", body_template="运营覆写文案：{{body}}",
            locale="zh-CN", status="active",
        )
        subject, body, html = notification_service._render_email_content(
            db, self._event(template_key="review_sla_due_soon"))
        self.assertEqual(subject, "到期：审核即将到期：合同 A（剩 6 小时）")
        self.assertIn("运营覆写文案", body)
        self.assertIsNone(html)
