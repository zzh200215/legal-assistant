"""E-5 可观测性：Sentry 错误上报 + OpenTelemetry 链路追踪。

所有依赖均为延迟 import，且按配置开关初始化：
- SENTRY_DSN 留空 → 不初始化 Sentry，零额外依赖。
- OTEL_ENABLED=False 或未配置 OTLP 端点 → 不初始化 OTel。
- 已配置但对应依赖未安装 → 捕获异常并警告，不阻断应用启动。

本地/测试环境未安装 sentry-sdk、opentelemetry 相关包时也可正常运行。
"""
import logging

logger = logging.getLogger(__name__)

_TRACES_SAMPLE_RATE = 0.1


def init_telemetry(app) -> None:
    """在 FastAPI app 创建后调用，安全地初始化 Sentry 与 OTel。"""
    _init_sentry(app)
    _init_otel(app)


def _init_sentry(app) -> None:
    try:
        from app.core.config import get_settings

        dsn = get_settings().SENTRY_DSN.strip()
        if not dsn:
            return
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            traces_sample_rate=_TRACES_SAMPLE_RATE,
            environment=get_settings().ENVIRONMENT or "development",
        )
        logger.info("Sentry 已启用")
    except Exception as exc:
        logger.warning("Sentry 初始化失败（不影响启动）：%s", exc)


def _init_otel(app) -> None:
    try:
        from app.core.config import get_settings

        settings = get_settings()
        if not settings.OTEL_ENABLED or not settings.OTEL_EXPORTER_OTLP_ENDPOINT.strip():
            return
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({SERVICE_NAME: "aibg-api"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT.strip()))
        )
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor.instrument_app(app)
        try:
            from app.core.database import engine

            SQLAlchemyInstrumentor().instrument(engine=engine)
        except Exception:
            logger.warning("OTel SQLAlchemy 插桩失败（HTTP 追踪仍生效）", exc_info=True)
        logger.info("OpenTelemetry 已启用（endpoint=%s）", settings.OTEL_EXPORTER_OTLP_ENDPOINT)
    except Exception as exc:
        logger.warning("OpenTelemetry 初始化失败（不影响启动）：%s", exc)
