"""E-5 可观测性初始化 — 未配置时为零副作用 no-op。"""
import unittest

from fastapi import FastAPI

from app.core.telemetry import init_telemetry


class TelemetryInitTests(unittest.TestCase):
    def test_unconfigured_is_noop(self):
        # 默认配置（SENTRY_DSN 空、OTEL_ENABLED=False）下初始化不新增路由、不抛错
        app = FastAPI()
        before = len(app.routes)
        init_telemetry(app)
        self.assertEqual(len(app.routes), before)

    def test_idempotent_on_bare_app(self):
        # 重复调用同样安全（两个开关均关闭时无任何副作用）
        app = FastAPI()
        before = len(app.routes)
        init_telemetry(app)
        init_telemetry(app)
        self.assertEqual(len(app.routes), before)


if __name__ == "__main__":
    unittest.main()
