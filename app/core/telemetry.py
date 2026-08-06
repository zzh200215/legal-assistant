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
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({SERVICE_NAME: "aibg-api"})
        provider = TracerProvider(resource=resource)
        endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT.strip()
        # endpoint 带 http:// 前缀时按明文/HTTP 处理；否则（host:port）视为本地明文 gRPC，
        # 显式 insecure=True，避免 SDK 默认对无前缀 endpoint 启用 TLS 导致本地 collector 连接失败。
        insecure = not endpoint.startswith("https://")
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=insecure))
        )
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor.instrument_app(app)
        try:
            # SQLAlchemy 插桩依赖独立包：缺失时仅跳过 DB 追踪，不阻塞 HTTP 追踪
            from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
            from app.core.database import engine

            SQLAlchemyInstrumentor().instrument(engine=engine)
        except Exception:
            logger.warning("OTel SQLAlchemy 插桩失败（HTTP 追踪仍生效）", exc_info=True)
        logger.info("OpenTelemetry 已启用（endpoint=%s）", endpoint)
    except Exception as exc:
        logger.warning("OpenTelemetry 初始化失败（不影响启动）：%s", exc)
