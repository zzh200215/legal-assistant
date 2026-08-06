"""E-5 可观测性初始化 — 未配置时为零副作用 no-op。"""
import unittest
from unittest.mock import patch

from fastapi import FastAPI

from app.core.telemetry import init_telemetry, _init_otel


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

    def test_otel_insecure_for_plain_endpoint(self):
        # host:port（无 https:// 前缀）→ insecure=True，本地明文 gRPC collector
        app = FastAPI()
        with patch("app.core.config.get_settings") as mock_settings, \
             patch("opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter") as mock_exporter, \
             patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor"), \
             patch("opentelemetry.trace.set_tracer_provider"):
            mock_settings.return_value.OTEL_ENABLED = True
            mock_settings.return_value.OTEL_EXPORTER_OTLP_ENDPOINT = "localhost:4317"
            _init_otel(app)
        self.assertEqual(mock_exporter.call_args.kwargs["insecure"], True)

    def test_otel_tls_for_https_endpoint(self):
        # https:// 前缀 → insecure=False（TLS）
        app = FastAPI()
        with patch("app.core.config.get_settings") as mock_settings, \
             patch("opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter") as mock_exporter, \
             patch("opentelemetry.instrumentation.fastapi.FastAPIInstrumentor"), \
             patch("opentelemetry.trace.set_tracer_provider"):
            mock_settings.return_value.OTEL_ENABLED = True
            mock_settings.return_value.OTEL_EXPORTER_OTLP_ENDPOINT = "https://collector.example:4317"
            _init_otel(app)
        self.assertEqual(mock_exporter.call_args.kwargs["insecure"], False)


if __name__ == "__main__":
    unittest.main()
