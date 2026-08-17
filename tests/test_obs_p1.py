"""P1 可观测性·审计·运营分析 验收测试（覆盖 14 项验收点）。

运行：pytest tests/test_obs_p1.py 或 python -m unittest tests.test_obs_p1
"""
import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base


def _make_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _no_redis():
    return patch(
        "app.services.org.security_audit_service.redis_lib.from_url",
        side_effect=Exception("redis unavailable in tests"),
    )


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


# ── 1) API request_id/trace_id/user_id/org_id 生成、传递与记录 ───────────────

class TestContextPropagation(unittest.TestCase):
    def _make_app(self):
        from app.core.obs_middleware import ObservabilityContextMiddleware
        from app.core.oplog_middleware import OperationLogMiddleware

        app = FastAPI()
        app.add_middleware(OperationLogMiddleware)
        app.add_middleware(ObservabilityContextMiddleware)  # 最外层

        @app.get("/echo")
        def echo():
            from app.core.obs_context import get_context

            return get_context().to_dict()

        @app.get("/echo-auth")
        def echo_auth():
            from app.core.obs_context import enrich_context

            enrich_context(user_id=7, org_id=3)  # 模拟认证成功后注入身份
            from app.core.obs_context import get_context

            return get_context().to_dict()

        return TestClient(app)

    def test_external_ids_validated_and_preserved(self):
        client = self._make_app()
        resp = client.get("/echo", headers={"X-Request-Id": "reqtest12345678", "X-Trace-Id": "trctest12345678"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # P1 修复：OperationLogMiddleware 不再覆盖外部传入的 trace_id
        self.assertEqual(data["request_id"], "reqtest12345678")
        self.assertEqual(data["trace_id"], "trctest12345678")
        self.assertEqual(resp.headers.get("X-Request-Id"), "reqtest12345678")

    def test_invalid_external_ids_rejected_and_regenerated(self):
        client = self._make_app()
        resp = client.get("/echo", headers={"X-Request-Id": "<script>", "X-Trace-Id": "!!!"})
        data = resp.json()
        self.assertEqual(len(data["request_id"]), 32)
        self.assertNotEqual(data["trace_id"], "!!!")

    def test_identity_only_from_authenticated_context(self):
        client = self._make_app()
        # 外部传入 X-Obs-User-Id 等身份头不得被信任
        resp = client.get("/echo", headers={"X-Obs-User-Id": "999", "X-Obs-Org-Id": "888"})
        data = resp.json()
        self.assertIsNone(data["user_id"])
        self.assertIsNone(data["org_id"])
        # 认证成功后才注入
        resp = client.get("/echo-auth")
        data = resp.json()
        self.assertEqual(data["user_id"], 7)
        self.assertEqual(data["org_id"], 3)


# ── 2) Celery 重试/异步任务保留 trace_id/request_id/task_id ─────────────────

class TestCeleryContextRetention(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self._session_patches = [
            patch("app.tasks.signals.SessionLocal", self.Session),
        ]
        for p in self._session_patches:
            p.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for p in self._session_patches:
            p.stop()
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def _settings():
        s = MagicMock()
        s.TASK_DEFAULT_QUEUE = "default"
        return s

    @staticmethod
    def _fake_task(retries=0):
        return SimpleNamespace(
            name="parse_document",
            queue="document",
            request=SimpleNamespace(
                headers={
                    "X-Obs-Request-Id": "req12345678",
                    "X-Obs-Trace-Id": "trace12345678",
                    "X-Obs-User-Id": "7",
                    "X-Obs-Org-Id": "3",
                },
                retries=retries,
            ),
        )

    def test_prerun_restores_and_ledger_retains_ids_across_retry(self):
        from app.models.task_run import TaskRun
        from app.tasks import signals

        signals._on_task_prerun(task_id="tid-1", task=self._fake_task(retries=0), args=(1, 1, "pdf"))
        row = self.db.query(TaskRun).filter(TaskRun.task_id == "tid-1").first()
        self.assertIsNotNone(row)
        self.assertEqual(row.request_id, "req12345678")
        self.assertEqual(row.trace_id, "trace12345678")
        self.assertEqual(row.task_id, "tid-1")
        self.assertEqual(row.attempt, 1)

        # 重试：同 task_id 复用行、attempt 递增、关联 ID 不丢
        signals._on_task_prerun(task_id="tid-1", task=self._fake_task(retries=1), args=(1, 1, "pdf"))
        self.db.expire_all()
        row = self.db.query(TaskRun).filter(TaskRun.task_id == "tid-1").first()
        self.assertEqual(row.attempt, 2)
        self.assertEqual(row.request_id, "req12345678")
        self.assertEqual(row.trace_id, "trace12345678")

        signals._on_task_success(task_id="tid-1", task=self._fake_task())
        self.db.expire_all()
        row = self.db.query(TaskRun).filter(TaskRun.task_id == "tid-1").first()
        self.assertEqual(row.status, "succeeded")

    def test_postrun_resets_context(self):
        from app.core.obs_context import get_context, set_context, build_context
        from app.tasks import signals

        set_context(build_context(request_id="req12345678", trace_id="trace12345678"))
        signals._on_task_postrun()
        self.assertIsNone(get_context().request_id)


# ── 3) LLM/Agent/连接器/通知关联同一条 trace ────────────────────────────────

class TestTraceCorrelation(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_llm_log_carries_context(self):
        from app.core.obs_context import build_context, set_context
        from app.models.llm_call_log import LLMCallLog
        from app.services.llm.llm_observability_service import llm_observability_service

        set_context(build_context(request_id="req12345678", trace_id="trace12345678", org_id=3))
        with patch("app.services.llm.llm_observability_service.SessionLocal", self.Session):
            llm_observability_service.log_event(
                module_name="chat", action="chat", model_name="qwen-plus",
                status="success", user_id=1, duration_ms=120,
            )
        row = self.db.query(LLMCallLog).first()
        self.assertEqual(row.trace_id, "trace12345678")
        self.assertEqual(row.organization_id, 3)

    def test_notification_and_connector_carry_trace(self):
        from app.core.obs_context import build_context, set_context
        from app.models.connector import ExternalConnector
        from app.services.integration.connector_sync_framework import get_or_create_run
        from app.services.notification.notification_service import notification_service

        set_context(build_context(request_id="req12345678", trace_id="trace12345678"))
        event = notification_service.create_notification(
            db=self.db, organization_id=3, user_id=1,
            event_type="deadline", title="提醒", channel="site",
        )
        self.assertEqual(event.trace_id, "trace12345678")
        self.assertEqual(event.request_id, "req12345678")

        connector = ExternalConnector(
            user_id=1, organization_id=3, connector_type="mock", name="mock", status="active",
        )
        self.db.add(connector)
        self.db.commit()
        run = get_or_create_run(self.db, connector=connector, owner="test", sync_mode="manual", ttl_seconds=60)
        self.assertEqual(run.trace_id, "trace12345678")
        self.db.delete(run)
        self.db.delete(connector)
        self.db.commit()

    def test_agent_run_carries_trace(self):
        from app.services.agent.agent_run_repository import run_state_repository

        run = run_state_repository.create_run(
            self.db, goal="测试目标", user_id=1,
            trace_id="trace12345678", organization_id=3,
        )
        self.assertEqual(run.trace_id, "trace12345678")
        self.assertEqual(run.organization_id, 3)


# ── 4) 五类日志分类 + 5) 敏感字段统一脱敏 ────────────────────────────────────

class TestLogClassificationAndRedaction(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _settings(self, enabled=True):
        s = MagicMock()
        s.STRUCTURED_LOG_JSON_LINES = enabled
        s.STRUCTURED_LOG_FILE = ""
        return s

    def _capture(self):
        logger = logging.getLogger("audit.json")
        handler = _CaptureHandler()
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        return logger, handler

    def test_five_log_types_classified(self):
        from app.core.observability import structured_observe

        logger, handler = self._capture()
        try:
            with patch("app.core.observability.get_settings", return_value=self._settings()):
                structured_observe(log_type="access", event_name="http_request", level="info")
                structured_observe(log_type="business", event_name="doc_created", level="info")
                structured_observe(log_type="security", event_name="login_failed", level="warn")
                structured_observe(log_type="audit", event_name="user_disable", level="info")
                structured_observe(log_type="model", event_name="chat", level="info")
        finally:
            logger.removeHandler(handler)
        types = {json.loads(line)["log_type"] for line in handler.records}
        self.assertEqual(types, {"access", "business", "security", "audit", "model"})

    def test_contract_and_secrets_never_enter_logs(self):
        from app.core.observability import structured_observe

        logger, handler = self._capture()
        try:
            with patch("app.core.observability.get_settings", return_value=self._settings()):
                structured_observe(
                    log_type="business", event_name="doc_created", level="info",
                    detail="合同正文：" + "甲方应支付100万元。" * 400,
                    extra={"password": "P@ssw0rd12345", "api_key": "sk-abcdefghijklmnop123456", "ok": 1},
                )
        finally:
            logger.removeHandler(handler)
        self.assertEqual(len(handler.records), 1)
        payload = json.loads(handler.records[0])
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("P@ssw0rd12345", blob)
        self.assertNotIn("sk-abcdefghijklmnop123456", blob)
        self.assertNotIn("甲方应支付100万元", blob)
        self.assertEqual(payload["extra"]["password"], "****redacted****")
        self.assertEqual(payload["extra"]["api_key"], "****redacted****")

    def test_redact_payload_recursive_and_cycle_safe(self):
        from app.core.observability_sanitizer import redact_payload

        payload = {
            "content": "合同全文" * 501,
            "password": "secret1",
            "nested": {"client_secret": "s2", "user_id": 3},
            "items": [{"token": "t3", "keep": "v"}],
            "hashme": "idcard123",
        }
        out = redact_payload(payload, allowed_keys={"keep", "nested", "items"}, hash_keys={"hashme"})
        self.assertEqual(out["password"], "****redacted****")
        self.assertEqual(out["nested"]["client_secret"], "****redacted****")
        self.assertEqual(out["items"][0]["token"], "****redacted****")
        self.assertEqual(out["items"][0]["keep"], "v")
        self.assertNotEqual(out["hashme"], "idcard123")
        self.assertIn("redacted:len", out["content"])
        # 循环引用：深度上限兜底，不递归爆栈
        cyclic = {}
        cyclic["self"] = cyclic
        out2 = redact_payload(cyclic)
        self.assertIsInstance(out2["self"], str)

    def test_oplog_and_audit_db_rows_sanitized(self):
        from app.models.user import User, UserStatus
        from app.services.observability.audit_log_service import audit_log_service
        from app.services.observability.oplog_service import oplog_service

        user = User(username="op1", email="op1@t.com", hashed_password="x",
                    role="admin", status=UserStatus.active.value)
        self.db.add(user)
        self.db.commit()
        oplog_service.log(module="test", action="create", db=self.db, user_id=user.id,
                          detail="操作详情 password=SuperSecret99 " + "正文" * 300)
        audit_log_service.log(db=self.db, operator=user, action="user_disable",
                              detail="禁用账号 token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnopqrstuvwxyz123456")
        from app.models.operation_log import OperationLog

        op = self.db.query(OperationLog).first()
        self.assertNotIn("SuperSecret99", op.detail or "")
        self.assertLessEqual(len(op.detail or ""), 2000)
        from app.models.auth_log import AdminAuditLog

        au = self.db.query(AdminAuditLog).first()
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", au.detail or "")

    def test_llm_log_excerpt_never_stored(self):
        from app.core.obs_context import build_context, set_context
        from app.models.llm_call_log import LLMCallLog
        from app.services.llm.llm_observability_service import llm_observability_service

        set_context(build_context(request_id="req12345678", trace_id="trace12345678"))
        with patch("app.services.llm.llm_observability_service.SessionLocal", self.Session):
            llm_observability_service.log_event(
                module_name="chat", action="chat", model_name="qwen-plus",
                status="failed", user_id=1,
                error_message="上游超时，请重试",
                request_excerpt={"content": "完整 prompt 包含合同敏感内容" * 50, "password": "x"},
                response_excerpt="模型完整回复原文" * 50,
            )
        row = self.db.query(LLMCallLog).first()
        self.assertEqual(row.trace_id, "trace12345678")
        self.assertNotIn("完整 prompt 包含合同敏感内容", row.request_excerpt or "")
        self.assertNotIn("模型完整回复原文", row.response_excerpt or "")
        self.assertNotIn("password", row.request_excerpt or "")


# ── 6/7) SLO 指标口径 + 高基数标签约束 ───────────────────────────────────────

class TestSloMetrics(unittest.TestCase):
    NOW = datetime(2026, 9, 20, 10, 0, 0)  # naive UTC

    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self._seed()

    def _seed(self):
        from app.models.agent import AgentRun
        from app.models.cost_ledger import CostLedgerEntry
        from app.models.document import Document, DocumentParseJob
        from app.models.legal_notifications import LegalNotificationEvent
        from app.models.llm_call_log import LLMCallLog
        from app.models.task_run import TaskRun
        from app.models.token_usage import TokenUsage
        from app.models.user import User

        t = self.NOW - timedelta(minutes=45)  # 落在 09:00 桶内
        self.db.add(User(id=1, username="u1", email="u1@t.com", hashed_password="x",
                         role="admin", status="active", organization_id=1))
        for status in ("success", "success", "failed", "blocked"):
            self.db.add(LLMCallLog(user_id=1, module_name="chat", action="chat",
                                   model_name="qwen-plus", status=status, created_at=t))
        self.db.add(Document(id=1, user_id=1, file_type="pdf", mime_type="application/pdf",
                             title="t", status="indexed", created_at=t))
        for status in ("succeeded", "succeeded", "succeeded", "failed"):
            self.db.add(DocumentParseJob(document_id=1, user_id=1, job_type="document_parse",
                                         status=status, started_at=t, created_at=t))
        self.db.add(DocumentParseJob(document_id=1, user_id=1, job_type="document_parse",
                                     status="pending", started_at=None, created_at=t))
        for status in ("completed", "completed", "completed", "error", "cancelled", "cancelled"):
            self.db.add(AgentRun(user_id=1, goal="g", status=status, organization_id=3, created_at=t))
        for status in ("sent", "sent", "delivered", "failed", "dead_letter", "pending"):
            self.db.add(LegalNotificationEvent(organization_id=3, user_id=1, event_type="deadline",
                                               title="t", channel="site", status=status, created_at=t))
        for index, status in enumerate(("succeeded", "succeeded", "succeeded", "succeeded", "failed", "failed", "retrying")):
            self.db.add(TaskRun(task_id=f"tid-{status}-{index}", task_name="parse_document",
                                status=status, queue="document", scope="document", created_at=t))
        tu = TokenUsage(user_id=1, model="qwen-plus", total_tokens=100)
        self.db.add(tu)
        self.db.flush()
        self.db.add(CostLedgerEntry(entry_id="e1", tenant_id=3, user_id=1, entry_type="llm_call",
                                    direction="cost", amount=0.500000, currency="CNY", scope="llm_cost",
                                    idempotency_key="u1", source_type="llm_run", source_id=str(tu.id),
                                    occurred_at=t, created_at=t))
        self.db.add(CostLedgerEntry(entry_id="e2", tenant_id=3, user_id=1, entry_type="llm_call",
                                    direction="cost", amount=0.750000, currency="CNY", scope="llm_cost",
                                    idempotency_key="u2", source_type="llm_run", source_id=str(tu.id),
                                    occurred_at=t, created_at=t))
        self.db.commit()

    def _hourly(self, metric):
        from app.models.ops_metric import OpsMetricHourly

        return self.db.query(OpsMetricHourly).filter(OpsMetricHourly.metric_name == metric).all()

    def _totals(self, metric):
        rows = self._hourly(metric)
        numerator = sum(float(r.numerator or 0) for r in rows)
        denominator = sum(float(r.denominator or 0) for r in rows)
        count = sum(float(r.count or 0) for r in rows)
        return numerator, denominator, count

    def test_llm_success_rate(self):
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        ops_aggregation_service.aggregate_metric(self.db, "hour", "llm_calls", now=self.NOW)
        num, den, count = self._totals("llm_calls")
        self.assertEqual(num, 2)   # success
        self.assertEqual(den, 3)   # 排除 blocked
        self.assertEqual(count, 4)

    def test_doc_parse_rate(self):
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        ops_aggregation_service.aggregate_metric(self.db, "hour", "doc_parse_jobs", now=self.NOW)
        num, den, count = self._totals("doc_parse_jobs")
        self.assertEqual(num, 3)
        self.assertEqual(den, 4)   # 已开始且终态；pending/未开始不计
        self.assertEqual(count, 4)

    def test_agent_completion_rate_cancelled_excluded(self):
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        ops_aggregation_service.aggregate_metric(self.db, "hour", "agent_runs", now=self.NOW)
        num, den, count = self._totals("agent_runs")
        self.assertEqual(num, 3)
        self.assertEqual(den, 4)   # cancelled 默认排除
        self.assertEqual(count, 4)

    def test_notification_delivery_rate(self):
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        ops_aggregation_service.aggregate_metric(self.db, "hour", "notification_deliveries", now=self.NOW)
        num, den, count = self._totals("notification_deliveries")
        self.assertEqual(num, 3)   # sent+delivered
        self.assertEqual(den, 5)   # sent+delivered+failed+dead_letter；pending 不计
        self.assertEqual(count, 5)

    def test_task_success_rate(self):
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        ops_aggregation_service.aggregate_metric(self.db, "hour", "task_outcomes", now=self.NOW)
        num, den, count = self._totals("task_outcomes")
        self.assertEqual(num, 4)
        self.assertEqual(den, 6)   # retrying 非终态不计
        self.assertEqual(count, 6)

    def test_model_cost_decimal(self):
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        ops_aggregation_service.aggregate_metric(self.db, "hour", "model_cost", now=self.NOW)
        rows = self._hourly("model_cost")
        self.assertEqual(sum(float(r.count or 0) for r in rows), 2)
        self.assertEqual(sum(float(r.cost_value or 0) for r in rows), 1.25)

    def test_api_p95_and_backlog_from_snapshots(self):
        from app.models.ops_metric import OpsMetricSnapshot
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        t = self.NOW - timedelta(minutes=30)
        self.db.add(OpsMetricSnapshot(bucket_start=t, metric_name="api_request_duration", org_id=None,
                                      kind="histogram", labels_json='{"route":"/api/chat"}',
                                      count=100, sum_value=5000, p95_value=250))
        self.db.add(OpsMetricSnapshot(bucket_start=t, metric_name="task_backlog", org_id=None,
                                      kind="gauge", labels_json='{"queue":"document","state":"in_flight"}',
                                      count=5))
        self.db.add(OpsMetricSnapshot(bucket_start=t, metric_name="task_backlog", org_id=None,
                                      kind="gauge", labels_json='{"queue":"document","state":"in_flight"}',
                                      count=7))
        self.db.commit()
        ops_aggregation_service.aggregate_metric(self.db, "hour", "api_request_duration", now=self.NOW)
        ops_aggregation_service.aggregate_metric(self.db, "hour", "task_backlog", now=self.NOW)
        p95_rows = self._hourly("api_request_duration")
        self.assertEqual(float(p95_rows[0].count), 100)
        self.assertEqual(float(p95_rows[0].sum_value), 5000)
        self.assertEqual(float(p95_rows[0].p95_value), 250)
        backlog_rows = self._hourly("task_backlog")
        self.assertEqual(float(backlog_rows[0].max_value), 7)

    def test_aggregation_idempotent_and_watermark(self):
        from app.models.ops_metric import OpsMetricWatermark
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        r1 = ops_aggregation_service.aggregate_metric(self.db, "hour", "llm_calls", now=self.NOW)
        first = [(float(r.numerator or 0), float(r.denominator or 0), float(r.count or 0))
                 for r in self._hourly("llm_calls")]
        r2 = ops_aggregation_service.aggregate_metric(self.db, "hour", "llm_calls", now=self.NOW)
        second = [(float(r.numerator or 0), float(r.denominator or 0), float(r.count or 0))
                  for r in self._hourly("llm_calls")]
        self.assertEqual(first, second)          # 重复执行不重复累加
        self.assertGreaterEqual(r1["buckets"], 1)
        self.assertEqual(r2["buckets"], 0)       # 水位线推进后无新桶
        wm = self.db.query(OpsMetricWatermark).filter(
            OpsMetricWatermark.granularity == "hour",
            OpsMetricWatermark.metric_name == "llm_calls",
        ).first()
        self.assertIsNotNone(wm)

    def test_slo_rates_read_aggregates_only(self):
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        ops_aggregation_service.aggregate_metric(self.db, "hour", "llm_calls", now=self.NOW)
        # 天级：行所在天（09-20）须完整，用次日零时作为聚合锚点
        day_now = self.NOW.replace(day=21, hour=0, minute=0, second=0, microsecond=0)
        ops_aggregation_service.aggregate_metric(self.db, "day", "llm_calls", now=day_now)
        stats = ops_aggregation_service.slo_rates(self.db, metric_name="llm_calls", days=30)
        # slo_rates 按 (org, labels) 分组；整体口径 = Σnumerator / Σdenominator
        self.assertGreaterEqual(len(stats["items"]), 1)
        numerator = sum(item["numerator"] for item in stats["items"])
        denominator = sum(item["denominator"] for item in stats["items"])
        self.assertEqual(numerator, 2)
        self.assertEqual(denominator, 3)
        self.assertAlmostEqual(numerator / denominator, 2 / 3)

    def test_high_cardinality_labels_rejected(self):
        from app.core.metrics import metrics

        metrics.snapshot_and_reset()  # 排空前序测试残留（模块级单例注册表）
        with patch("app.core.config.get_settings") as mock_get:
            mock_get.return_value = MagicMock(OBS_METRICS_SNAPSHOT_ENABLED=True)
            metrics.increment("test_metric", labels={
                "request_id": "req1", "trace_id": "trc1", "user_id": 7,
                "document_id": 9, "model": "qwen-plus",
            })
            items = metrics.snapshot_and_reset()
        self.assertEqual(len(items), 1)
        labels = items[0]["labels"]
        self.assertNotIn("request_id", labels)
        self.assertNotIn("trace_id", labels)
        self.assertNotIn("user_id", labels)
        self.assertNotIn("document_id", labels)
        self.assertEqual(labels.get("model"), "qwen-plus")


# ── 10/11) 审计：append-only、hash chain、篡改检测、导出 ──────────────────────

class TestAuditIntegrityAndExport(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self._redis_patch = _no_redis()
        self._redis_patch.start()
        # write_event/verify_chain 的 Redis 降级路径使用模块级 SessionLocal：指向测试会话
        self._session_patch = patch("app.services.org.security_audit_service.SessionLocal", self.Session)
        self._session_patch.start()
        self.addCleanup(self._redis_patch.stop)
        self.addCleanup(self._session_patch.stop)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_hash_chain_intact_then_tamper_detected(self):
        from app.models.legal_notifications import SecurityAuditEvent
        from app.services.org.security_audit_service import verify_chain, write_event

        for i in range(3):
            write_event(db=self.db, event_type="permission_change", actor_type="user",
                        actor_id="1", result="success", organization_id=3,
                        action="role_change", reason_code="ok")
        result = verify_chain(organization_id=3)
        self.assertTrue(result["intact"], result)
        self.assertEqual(result["total"], 3)

        # 篡改：直接改库（业务层无 UPDATE/DELETE 路径）
        self.db.query(SecurityAuditEvent).filter(SecurityAuditEvent.seq_no == 2).update(
            {"action": "tampered_by_attacker"}
        )
        self.db.commit()
        result = verify_chain(organization_id=3)
        self.assertFalse(result["intact"])
        self.assertEqual([item["seq_no"] for item in result["broken"]], [2])

    def test_org_filtered_chain_with_global_predecessor(self):
        from app.services.org.security_audit_service import verify_chain, write_event

        write_event(db=self.db, event_type="login", actor_type="user", actor_id="1",
                    result="success", organization_id=1)
        write_event(db=self.db, event_type="login", actor_type="user", actor_id="2",
                    result="success", organization_id=2)
        write_event(db=self.db, event_type="login", actor_type="user", actor_id="1",
                    result="success", organization_id=1)
        self.assertTrue(verify_chain(organization_id=1)["intact"])
        self.assertTrue(verify_chain(organization_id=2)["intact"])
        self.assertTrue(verify_chain()["intact"])

    def test_block_failure_policy(self):
        from app.services.org import security_audit_service as svc

        with patch.object(svc, "_next_seq_no", side_effect=RuntimeError("db down")):
            with self.assertRaises(svc.AuditWriteError):
                svc.write_event(db=self.db, event_type="export", actor_type="user",
                                actor_id="1", result="success", organization_id=3)
            # 普通事件 degrade：返回 None 不抛（但已记录日志，不静默）
            result = svc.write_event(db=self.db, event_type="login", actor_type="user",
                                     actor_id="1", result="success", organization_id=3)
            self.assertIsNone(result)

    def test_export_job_streams_manifest_and_audits(self):
        from app.models.legal_notifications import SecurityAuditEvent
        from app.models.legal_platform import LegalAsyncJob
        from app.services.observability.audit_export_service import audit_export_service
        from app.services.org.security_audit_service import write_event

        for i in range(6):
            write_event(db=self.db, event_type="login", actor_type="user",
                        actor_id=str(i % 2 + 1), result="success", organization_id=5)

        with tempfile.TemporaryDirectory() as tmp:
            settings = MagicMock(OBS_AUDIT_ARCHIVE_DIR=str(Path(tmp) / "archives"))
            job = LegalAsyncJob(organization_id=5, job_type="audit_export", status="queued",
                                created_by=1, input_json=json.dumps({"organization_id": 5}))
            self.db.add(job)
            self.db.commit()
            with patch("app.services.observability.audit_export_service.get_settings", return_value=settings):
                result = audit_export_service.run_export_job(self.db, job.id)
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["count"], 6)
            self.db.expire_all()
            job = self.db.get(LegalAsyncJob, job.id)
            self.assertEqual(job.status, "succeeded")
            output = json.loads(job.output_json)
            data_path = Path(output["file"])
            self.assertTrue(data_path.exists())
            lines = data_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 6)
            manifest = json.loads((Path(output["manifest"])).read_text(encoding="utf-8"))
            self.assertEqual(manifest["record_count"], 6)
            self.assertEqual(manifest["sha256"], output["sha256"])
            self.assertEqual(manifest["format"], audit_export_service.EXPORT_FORMAT)
            # 导出事件入审计
            export_event = self.db.query(SecurityAuditEvent).filter(
                SecurityAuditEvent.action == "audit_export"
            ).first()
            self.assertIsNotNone(export_event)
            self.assertEqual(export_event.result, "success")

    def test_export_frozen_on_broken_chain(self):
        from app.models.legal_notifications import SecurityAuditEvent
        from app.models.legal_platform import LegalAsyncJob
        from app.services.observability.audit_export_service import audit_export_service
        from app.services.org.security_audit_service import write_event

        write_event(db=self.db, event_type="login", actor_type="user", actor_id="1",
                    result="success", organization_id=5)
        self.db.query(SecurityAuditEvent).update({"action": "tampered"})
        self.db.commit()

        with tempfile.TemporaryDirectory() as tmp:
            settings = MagicMock(OBS_AUDIT_ARCHIVE_DIR=str(Path(tmp) / "archives"))
            job = LegalAsyncJob(organization_id=5, job_type="audit_export", status="queued",
                                created_by=1, input_json=json.dumps({"organization_id": 5}))
            self.db.add(job)
            self.db.commit()
            with patch("app.services.observability.audit_export_service.get_settings", return_value=settings):
                result = audit_export_service.run_export_job(self.db, job.id)
            self.assertEqual(result["status"], "failed")
            self.db.expire_all()
            job = self.db.get(LegalAsyncJob, job.id)
            self.assertEqual(job.status, "failed")
            self.assertIn("chain_broken", job.error_summary)
            blocked = self.db.query(SecurityAuditEvent).filter(
                SecurityAuditEvent.result == "blocked"
            ).first()
            self.assertIsNotNone(blocked)


# ── 12) 保留策略：审计默认不物理删除、归档受控清理、预聚合保留 ────────────────

class TestRetentionPolicy(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self._redis_patch = _no_redis()
        self._redis_patch.start()
        self._session_patch = patch("app.services.org.security_audit_service.SessionLocal", self.Session)
        self._session_patch.start()
        self.addCleanup(self._redis_patch.stop)
        self.addCleanup(self._session_patch.stop)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _settings(self, archive_enabled, purge_after_archive, tmp):
        s = MagicMock()
        s.DATABASE_ARCHIVE_ENABLED = True
        s.DATABASE_ARCHIVE_DRY_RUN = False
        s.DATABASE_ARCHIVE_BATCH_SIZE = 50
        s.DATABASE_ARCHIVE_LOCK_TIMEOUT_MINUTES = 30
        s.archive_retention_days.return_value = {"admin_audit_logs": 30}
        s.OBS_AUDIT_ARCHIVE_ENABLED = archive_enabled
        s.OBS_AUDIT_PURGE_AFTER_ARCHIVE = purge_after_archive
        s.OBS_AUDIT_ARCHIVE_DIR = str(Path(tmp) / "archives")
        s.OBS_AUDIT_TABLE_RETENTION_CLASS_JSON = '{"admin_audit_logs": "default"}'
        s.OBS_AUDIT_RETENTION_DAYS_JSON = '{"default": 180}'
        s.OBS_METRICS_SNAPSHOT_RETENTION_DAYS = 30
        s.OBS_AGGREGATION_HOURLY_RETENTION_DAYS = 7
        s.OBS_AGGREGATION_DAILY_RETENTION_DAYS = 90
        s.OBS_WS_EVENT_RETENTION_DAYS = 7
        return s

    def _expired_audit_row(self):
        from app.models.auth_log import AdminAuditLog

        row = AdminAuditLog(operator_id=1, operator_name="old", action="user_disable",
                            created_at=_naive(datetime.now(timezone.utc) - timedelta(days=200)))
        self.db.add(row)
        self.db.commit()
        return row

    def test_audit_retained_by_default(self):
        from app.models.auth_log import AdminAuditLog
        from app.services.documents.archive_service import archive_service

        row = self._expired_audit_row()
        row_id = row.id
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.services.documents.archive_service.get_settings",
                       return_value=self._settings(False, False, tmp)):
                result = archive_service.run(self.db)
        self.assertEqual(result["tables"]["admin_audit_logs"]["status"], "retained_by_policy")
        self.db.expunge_all()
        still = self.db.query(AdminAuditLog).filter(AdminAuditLog.id == row_id).first()
        self.assertIsNotNone(still)  # 审计默认不物理删除

    def test_audit_archive_then_controlled_purge(self):
        from app.models.auth_log import AdminAuditLog
        from app.services.documents.archive_service import archive_service

        row = self._expired_audit_row()
        row_id = row.id
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.services.documents.archive_service.get_settings",
                       return_value=self._settings(True, False, tmp)):
                result = archive_service.run(self.db)
            status = result["tables"]["admin_audit_logs"]
            self.assertEqual(status["status"], "archived")
            self.assertGreaterEqual(status["archived"], 1)
            self.db.expunge_all()
            marked = self.db.query(AdminAuditLog).filter(AdminAuditLog.id == row_id).first()
            self.assertIsNotNone(marked)
            self.assertIsNotNone(marked.archived_at)  # 已归档标记，未删除
            # 归档文件 + 清单存在
            files = list((Path(tmp) / "archives").glob("admin_audit_logs_*.jsonl"))
            self.assertTrue(files)
            # 受控清理：PURGE_AFTER_ARCHIVE=true 后才删除已归档行
            with patch("app.services.documents.archive_service.get_settings",
                       return_value=self._settings(True, True, tmp)):
                archive_service.run(self.db)
            self.db.expunge_all()
            self.assertIsNone(self.db.query(AdminAuditLog).filter(AdminAuditLog.id == row_id).first())

    def test_ops_metrics_retention_cleanup(self):
        from app.models.ops_metric import OpsMetricSnapshot
        from app.services.documents.archive_service import archive_service

        now = _naive(datetime.now(timezone.utc))
        self.db.add(OpsMetricSnapshot(bucket_start=now - timedelta(days=60), metric_name="llm_calls",
                                      kind="counter", count=1))
        self.db.add(OpsMetricSnapshot(bucket_start=now - timedelta(days=1), metric_name="llm_calls",
                                      kind="counter", count=2))
        self.db.commit()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("app.services.documents.archive_service.get_settings",
                       return_value=self._settings(False, False, tmp)):
                archive_service.run(self.db)
        from app.models.ops_metric import OpsMetricSnapshot as M

        remaining = self.db.query(M).all()
        self.assertEqual(len(remaining), 1)  # 30 天前的快照被清理


# ── 13) 观测能力不可用不阻断业务；高风险审计 fail-closed ─────────────────────

class TestNonBlockingObservability(unittest.TestCase):
    def test_metrics_disabled_is_noop(self):
        from app.core.metrics import metrics

        with patch("app.core.config.get_settings") as mock_get:
            mock_get.return_value = MagicMock(OBS_METRICS_SNAPSHOT_ENABLED=False)
            metrics.increment("a", labels={"model": "m"})
            metrics.observe("b", 100)
            metrics.set_gauge("c", 1)
            self.assertEqual(metrics.snapshot_and_reset(), [])

    def test_otel_span_disabled_yields_none(self):
        from app.core.telemetry import observe_span

        with observe_span("test_span", attributes={"model": "m"}) as span:
            self.assertIsNone(span)  # OTel 默认关闭 → no-op

    def test_structured_log_disabled_emits_nothing(self):
        from app.core.observability import structured_observe

        logger = logging.getLogger("audit.json")
        handler = _CaptureHandler()
        logger.addHandler(handler)
        logger.propagate = False
        try:
            with patch("app.core.observability.get_settings") as mock_get:
                mock_get.return_value = MagicMock(STRUCTURED_LOG_JSON_LINES=False)
                structured_observe(log_type="business", event_name="x", detail="敏感内容")
        finally:
            logger.removeHandler(handler)
        self.assertEqual(handler.records, [])


# ── 14) 租户隔离：聚合统计与审计按 org 隔离 ──────────────────────────────────

class TestOrgIsolation(unittest.TestCase):
    NOW = datetime(2026, 9, 20, 10, 0, 0)

    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_slo_stats_org_filtered(self):
        from app.models.agent import AgentRun
        from app.services.observability.ops_aggregation_service import ops_aggregation_service

        t = self.NOW - timedelta(minutes=45)
        for org, statuses in ((1, ["completed", "completed", "error"]), (2, ["completed"])):
            for status in statuses:
                self.db.add(AgentRun(user_id=1, goal="g", status=status, organization_id=org, created_at=t))
        self.db.commit()
        ops_aggregation_service.aggregate_metric(self.db, "hour", "agent_runs", now=self.NOW)
        day_now = self.NOW.replace(day=21, hour=0, minute=0, second=0, microsecond=0)
        ops_aggregation_service.aggregate_metric(self.db, "day", "agent_runs", now=day_now)
        stats_org1 = ops_aggregation_service.slo_rates(self.db, metric_name="agent_runs", days=30, org_id=1)
        for item in stats_org1["items"]:
            self.assertEqual(item["org_id"], 1)
        self.assertEqual(sum(item["denominator"] for item in stats_org1["items"]), 3)
        stats_org2 = ops_aggregation_service.slo_rates(self.db, metric_name="agent_runs", days=30, org_id=2)
        self.assertEqual(sum(item["denominator"] for item in stats_org2["items"]), 1)


if __name__ == "__main__":
    unittest.main()
