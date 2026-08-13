"""通知模板测试：版本不可覆盖、locale fallback、参数校验、HTML 转义渲染。"""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal_notifications import NotificationTemplate
from app.services.notification_template_service import (
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
