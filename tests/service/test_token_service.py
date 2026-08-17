"""Service 层：token_service 成本计算 / 用量记录 / 统计聚合边界测试。

覆盖 app/services/billing/token_service.py：
- compute_cost：Decimal 精度、未配置模型按 0、畸形定价 JSON 容错；
- record：写入 TokenUsage（Float 成本列）+ 成本台账（Numeric(18,6) Decimal、
  同事务、来源去重）、显式成本覆盖；
- get_user_stats / get_global_stats：聚合口径（按 action/model/日期分组）。
"""

import json
import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.cost_ledger import CostLedgerEntry
from app.models.token_usage import TokenUsage
from app.services.billing import token_service as token_module
from app.services.billing.token_service import token_service

PRICING = json.dumps(
    {
        "qwen-plus": {"input_per_1k": "0.0008", "output_per_1k": "0.002"},
        "qwen-max": {"input_per_1k": "0.0015", "output_per_1k": "0.004"},
    }
)


class TokenServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.Session()

    def tearDown(self):
        self.db.close()

    # ── compute_cost ─────────────────────────────────────────────────────────
    def test_compute_cost_decimal_precision(self):
        with patch.object(token_module.settings, "LLM_MODEL_PRICING", PRICING):
            cost = token_service.compute_cost("qwen-plus", 1000, 500)
        # 1000/1000*0.0008 + 500/1000*0.002 = 0.0008 + 0.001 = 0.0018
        self.assertEqual(cost, Decimal("0.001800"))

    def test_compute_cost_zero_for_unknown_model(self):
        with patch.object(token_module.settings, "LLM_MODEL_PRICING", PRICING):
            self.assertEqual(token_service.compute_cost("unknown-model", 1000, 1000), Decimal("0"))

    def test_compute_cost_tolerates_malformed_pricing(self):
        with patch.object(token_module.settings, "LLM_MODEL_PRICING", "{not-json"):
            self.assertEqual(token_service.compute_cost("qwen-plus", 10, 10), Decimal("0"))

    # ── record ───────────────────────────────────────────────────────────────
    def test_record_persists_usage_and_ledger(self):
        with patch.object(token_module.settings, "LLM_MODEL_PRICING", PRICING):
            usage = token_service.record(
                "qwen-plus", db=self.db, user_id=7, action="legal_review",
                prompt_tokens=1000, completion_tokens=500, duration_ms=123,
            )
        self.assertIsNotNone(usage.id)
        self.assertEqual(usage.total_tokens, 1500)
        # TokenUsage.cost 为 Float 列（台账侧才是 Decimal）：按浮点近似断言
        self.assertAlmostEqual(float(usage.cost), 0.0018, places=6)
        # 成本台账：Numeric(18,6) Decimal 精度，同事务入账
        ledger = self.db.query(CostLedgerEntry).filter(CostLedgerEntry.source_type == "llm_run").first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.amount, Decimal("0.001800"))

    def test_record_explicit_cost_overrides_computation(self):
        usage = token_service.record(
            "qwen-plus", db=self.db, user_id=7, action="x",
            prompt_tokens=1000, completion_tokens=500, cost=Decimal("9.990000"),
        )
        self.assertAlmostEqual(float(usage.cost), 9.99, places=6)

    def test_record_without_user_skips_ledger(self):
        usage = token_service.record("qwen-plus", db=self.db, user_id=None, action="x", prompt_tokens=1)
        self.assertIsNotNone(usage.id)
        self.assertEqual(self.db.query(CostLedgerEntry).count(), 0)

    # ── 统计聚合 ─────────────────────────────────────────────────────────────
    def _seed_usage(self, *, model="qwen-plus", action="legal_review", tokens=1000, days_ago=0):
        usage = TokenUsage(
            user_id=7, model=model, action=action, budget_category="legal",
            attempt_number=1, prompt_tokens=tokens, completion_tokens=tokens // 2,
            total_tokens=tokens + tokens // 2, cost=0.0018,
            duration_ms=200, created_at=utc_now() - timedelta(days=days_ago),
        )
        self.db.add(usage)
        self.db.commit()
        return usage

    def test_get_user_stats_aggregates(self):
        self._seed_usage(action="legal_review", tokens=1000, days_ago=1)
        self._seed_usage(action="legal_review", tokens=2000, days_ago=1)
        self._seed_usage(action="contract_scan", tokens=500, days_ago=29)
        stats = token_service.get_user_stats(7, self.db, days=30)
        self.assertEqual(stats["total_calls"], 3)
        self.assertEqual(stats["total_tokens"], 5250)
        self.assertEqual(stats["by_action"]["legal_review"]["calls"], 2)
        self.assertEqual(stats["by_action"]["contract_scan"]["calls"], 1)
        self.assertGreater(stats["avg_duration_ms"], 0)

    def test_get_user_stats_respects_days_window(self):
        self._seed_usage(days_ago=1)
        self._seed_usage(days_ago=31)
        stats = token_service.get_user_stats(7, self.db, days=30)
        self.assertEqual(stats["total_calls"], 1)

    def test_get_user_stats_empty(self):
        stats = token_service.get_user_stats(999, self.db, days=30)
        self.assertEqual(stats["total_calls"], 0)
        self.assertEqual(stats["avg_duration_ms"], 0)

    def test_get_global_stats_groups_by_model(self):
        self._seed_usage(model="qwen-plus")
        self._seed_usage(model="qwen-max")
        stats = token_service.get_global_stats(self.db, days=30)
        self.assertEqual(stats["total_calls"], 2)
        self.assertEqual(set(stats["by_model"].keys()), {"qwen-plus", "qwen-max"})


if __name__ == "__main__":
    unittest.main()
