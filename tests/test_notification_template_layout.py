"""通知模板 body_format：layout 文案覆写保留版式、html 自定义版式、text 保持旧行为。"""
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal_notifications import NotificationTemplate
from app.services.notification.email_templates import (
    LayoutOverrideError, html_to_text, parse_layout_overrides, render_builtin_email,
)
from app.services.notification.notification_service import notification_service
from app.services.notification.notification_template_service import (
    TemplateValidationError, notification_template_service,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _event(template_key):
    from app.models.legal_notifications import LegalNotificationEvent

    return LegalNotificationEvent(
        organization_id=1, user_id=1, event_type="approval",
        title="审核即将到期：合同 A（剩 6 小时）", body="sla:due_soon · 合同 #1",
        channel="email", template_key=template_key, reference_type="contract_review",
        reference_id=1,
    )


class LayoutOverrideTests(unittest.TestCase):
    def test_parse_accepts_whitelisted_fields(self):
        overrides = parse_layout_overrides(
            '{"lead": "新的导语", "action_label": "现在处理", "footnote": "新的说明"}')
        self.assertEqual(overrides["lead"], "新的导语")
        self.assertEqual(overrides["action_label"], "现在处理")

    def test_parse_rejects_unknown_field_bad_json_and_too_long(self):
        with self.assertRaises(LayoutOverrideError):
            parse_layout_overrides('{"nope": "x"}')
        with self.assertRaises(LayoutOverrideError):
            parse_layout_overrides("not json")
        with self.assertRaises(LayoutOverrideError):
            parse_layout_overrides(json.dumps({"lead": "长" * 400}, ensure_ascii=False))
        with self.assertRaises(LayoutOverrideError):
            parse_layout_overrides('{"lead": 123}')

    def test_empty_values_fall_back_to_builtin(self):
        self.assertEqual(parse_layout_overrides("{}"), {})
        self.assertEqual(parse_layout_overrides('{"lead": "  "}'), {})

    def test_override_keeps_layout_structure(self):
        rendered = render_builtin_email(
            "review_sla_due_soon",
            params={"title": "合同 A", "body": "正文", "action_url": "https://legal.example.com/x"},
            layout_overrides={"lead": "新导语", "action_label": "现在处理"},
        )
        self.assertIn('role="presentation"', rendered["html"])
        self.assertIn("新导语", rendered["html"])
        self.assertIn("现在处理", rendered["html"])
        self.assertNotIn("以下审核任务即将到期", rendered["html"], "被覆写的文案不应残留")
        self.assertIn("新导语", rendered["text"])

    def test_html_to_text_strips_tags_and_scripts(self):
        text = html_to_text("<p>第一段 &amp; 符号</p><script>alert(1)</script><div>第二段</div>")
        self.assertEqual(text.splitlines(), ["第一段 & 符号", "第二段"])


class TemplateFormatRenderingTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_body_format_change_creates_new_version(self):
        v1 = notification_template_service.create_template(
            db=self.db, channel="email", template_key="review_sla_due_soon",
            body_template="纯文本 {{body}}", subject_template="提醒 {{title}}", locale="zh-CN",
        )
        self.assertEqual(v1.body_format, "text")
        v2 = notification_template_service.create_template(
            db=self.db, channel="email", template_key="review_sla_due_soon",
            body_template="纯文本 {{body}}", subject_template="提醒 {{title}}", locale="zh-CN",
            body_format="text",
        )
        self.assertEqual(v2.version, 1, "同内容同格式应复用")
        v3 = notification_template_service.create_template(
            db=self.db, channel="email", template_key="review_sla_due_soon",
            body_template=json.dumps({"lead": "导语"}, ensure_ascii=False),
            subject_template="提醒 {{title}}", locale="zh-CN", body_format="layout",
        )
        self.assertEqual(v3.version, 2, "格式变化必须产生新版本")

    def test_invalid_body_format_rejected(self):
        with self.assertRaises(TemplateValidationError):
            notification_template_service.create_template(
                db=self.db, channel="email", template_key="k", body_template="b",
                body_format="markdown",
            )

    def test_layout_template_applies_copy_but_keeps_html(self):
        notification_template_service.create_template(
            db=self.db, channel="email", template_key="review_sla_due_soon",
            body_template=json.dumps({"lead": "运营改的导语", "footnote": "运营改的说明"},
                                     ensure_ascii=False),
            subject_template="【律智检】{{title}}", locale="zh-CN", body_format="layout",
            status="active",
        )
        subject, text, html = notification_service._render_email_content(
            self.db, _event("review_sla_due_soon"))
        self.assertEqual(subject, "【律智检】审核即将到期：合同 A（剩 6 小时）")
        self.assertIsNotNone(html)
        self.assertIn("运营改的导语", html)
        self.assertIn("运营改的说明", html)
        self.assertIn('role="presentation"', html, "版式结构应保留")
        self.assertIn("运营改的导语", text)

    def test_html_template_uses_custom_body_with_text_fallback(self):
        notification_template_service.create_template(
            db=self.db, channel="email", template_key="consultation_returned",
            body_template="<h1>被退回：{{title}}</h1><p>{{body}}</p>",
            subject_template="退回通知", locale="zh-CN", body_format="html", status="active",
        )
        subject, text, html = notification_service._render_email_content(
            self.db, _event("consultation_returned"))
        self.assertEqual(subject, "退回通知")
        self.assertIn("<h1>被退回", html)
        self.assertIn("审核即将到期：合同 A（剩 6 小时）", text)
        self.assertNotIn("<h1>", text, "纯文本回退必须剥标签")

    def test_broken_layout_json_falls_back_to_builtin(self):
        notification_template_service.create_template(
            db=self.db, channel="email", template_key="review_sla_due_soon",
            body_template="{bad json", subject_template="【律智检】{{title}}",
            locale="zh-CN", body_format="layout", status="active",
        )
        subject, text, html = notification_service._render_email_content(
            self.db, _event("review_sla_due_soon"))
        self.assertIsNotNone(html)
        self.assertIn("以下审核任务即将到期", html, "坏覆写退回内置版式")
        self.assertIn("以下审核任务即将到期", text)

    def test_activate_retires_previous_active_version(self):
        first = notification_template_service.create_template(
            db=self.db, channel="email", template_key="review_sla_due_soon",
            body_template=json.dumps({"lead": "v1"}, ensure_ascii=False), locale="zh-CN",
            body_format="layout", status="active",
        )
        second = notification_template_service.create_template(
            db=self.db, channel="email", template_key="review_sla_due_soon",
            body_template=json.dumps({"lead": "v2"}, ensure_ascii=False), locale="zh-CN",
            body_format="layout", status="draft",
        )
        notification_template_service.activate(
            db=self.db, channel="email", template_key="review_sla_due_soon",
            locale="zh-CN", version=second.version,
        )
        self.db.refresh(first)
        self.assertEqual(first.status, "retired")
        active_rows = self.db.query(NotificationTemplate).filter(
            NotificationTemplate.template_key == "review_sla_due_soon",
            NotificationTemplate.status == "active",
        ).all()
        self.assertEqual([row.version for row in active_rows], [second.version])


if __name__ == "__main__":
    unittest.main()
