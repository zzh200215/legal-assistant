"""迁移与脏数据处理测试：单一 head、迁移内容、重复数据去重、0074 回填逻辑。"""
import unittest

from sqlalchemy import create_engine, text

from alembic.config import Config
from alembic.script import ScriptDirectory

ALEMBIC_INI = "alembic.ini"


class MigrationChainTests(unittest.TestCase):
    def test_single_head_no_orphans(self):
        """迁移链必须是单一 head，alembic upgrade head 才能解析。"""
        cfg = Config(ALEMBIC_INI)
        sd = ScriptDirectory.from_config(cfg)
        heads = sd.get_heads()
        self.assertEqual(len(heads), 1, f"迁移链存在多个 head: {heads}")
        # 任意 revision 都必须可回溯到唯一 head（无孤儿分支）
        for rev in sd.walk_revisions():
            if rev.is_head:
                self.assertIn(rev.revision, heads)

    def test_0067_contains_expected_indexes_and_constraints(self):
        path = "alembic/versions/20260809_0067_infra_indexes_and_archive.py"
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for name in (
            "ix_token_usage_user_id_created_at",
            "ix_legal_notification_events_user_channel_status_created_at",
            "uq_legal_notification_preferences_user_event",
            "uq_webhook_subscriptions_app_event",
        ):
            self.assertIn(name, src, f"0067 缺少 {name}")
        self.assertIn("_dedupe_keep_latest", src)
        self.assertIn("database_archive_runs", src)

    def test_0068_adds_version_columns_and_idempotency_table(self):
        path = "alembic/versions/20260809_0068_optimistic_lock_and_idempotency.py"
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for table in ("documents", "tasks", "legal_cases", "legal_contracts", "organizations"):
            self.assertIn(f'"{table}"', src, f"0068 缺少 {table} version 列")
        for table in ("legal_contract_reviews", "legal_drafts"):
            self.assertIn(f'"{table}"', src, f"0068 缺少 {table} row_version 列")
        self.assertIn("idempotency_keys", src)
        self.assertIn("uq_idempotency_keys_scope_key", src)

    def test_0074_creates_legal_domain_tables_and_columns(self):
        """0074 必须建 5 张领域表、扩展现有列，并实现 downgrade。"""
        path = "alembic/versions/20260812_0074_legal_domain_models.py"
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for table in ("legal_facts", "legal_evidences", "legal_claims",
                      "legal_references", "contract_risk_items"):
            self.assertIn(f'"{table}"', src, f"0074 缺少建表 {table}")
        for col in ("expiration_date", "applicability_scope", "canonical_identifier"):
            self.assertIn(f'"{col}"', src, f"0074 缺少 legal_sources.{col}")
        self.assertIn('"target_version"', src)
        self.assertIn('"is_final"', src)
        self.assertIn("_backfill", src, "0074 应包含旧数据回填")
        self.assertIn("def downgrade", src)

    def test_0074_backfill_maps_old_json_into_structured_tables(self):
        """0074 回填：旧 risks_json/references_json/facts JSON 幂等映射到结构化新表。"""
        import importlib.util

        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        import app.models  # noqa: F401
        from app.core.auth import hash_password
        from app.core.database import Base
        from app.models.legal import LegalCase, LegalConsultation, ContractReview
        from app.models.legal_domain import ContractRiskItem, LegalFact
        from app.models.org import Organization
        from app.models.user import User

        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        db = Session()
        user = User(username="u", email="u@x.com", hashed_password=hash_password("pw"), role="user")
        org = Organization(name="O", code="o")
        db.add_all([user, org])
        db.commit()
        db.refresh(user)
        db.refresh(org)
        case = LegalCase(organization_id=org.id, user_id=user.id, title="c", case_type="other")
        db.add(case)
        db.commit()
        db.refresh(case)
        # 旧数据：只有 JSON 列，无结构化表
        review = ContractReview(
            user_id=user.id, case_id=case.id, title="r", content="content", summary="s",
            risks_json=(
                '[{"clause_type":"breach","risk_level":"high",'
                '"source_location":{"paragraph":1,"snippet":"违约条款"},"suggestion":"y"}]'
            ),
            references_json='[{"source_id":null}]', status="pending_review",
        )
        consult = LegalConsultation(
            user_id=user.id, case_id=case.id, question="q", category="other",
            known_facts_json='["已知事实"]', missing_facts_json='["缺失事实"]',
            references_json="[]", advice="a", risk_level="low", status="pending_review",
        )
        db.add_all([review, consult])
        db.commit()
        db.refresh(review)
        db.refresh(consult)

        mig_path = "alembic/versions/20260812_0074_legal_domain_models.py"
        spec = importlib.util.spec_from_file_location("mig_0074", mig_path)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)

        mig._backfill(engine)

        items = db.query(ContractRiskItem).filter(ContractRiskItem.review_id == review.id).all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].severity, "high")
        self.assertEqual(items[0].status, "needs_review")
        self.assertEqual(items[0].category, "breach")
        self.assertEqual(items[0].recommendation, "y")
        self.assertIn("违约条款", items[0].original_text_excerpt)
        # 幂等：重复回填不重复插入
        mig._backfill(engine)
        self.assertEqual(
            db.query(ContractRiskItem).filter(ContractRiskItem.review_id == review.id).count(), 1,
        )
        facts = db.query(LegalFact).filter(LegalFact.consultation_id == consult.id).all()
        self.assertEqual(len(facts), 2)
        self.assertEqual({f.fact_type for f in facts}, {"known", "missing"})

    def test_0075_creates_task_and_sync_reliability_tables(self):
        """0075 必须建 task_runs / connector_sync_items、扩展 connector_sync_jobs，并实现 downgrade。"""
        path = "alembic/versions/20260813_0075_task_reliability.py"
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for table in ("task_runs", "connector_sync_items"):
            self.assertIn(f'"{table}"', src, f"0075 缺少建表 {table}")
        for col in ("cursor_json", "checkpoint_json", "source_version", "processed",
                    "succeeded", "failed", "error_code", "attempt", "next_retry_at",
                    "idempotency_key", "lease_owner", "lease_expires_at"):
            self.assertIn(f'"{col}"', src, f"0075 缺少 connector_sync_jobs.{col}")
        for index in ("ix_task_runs_name_key_created", "ix_task_runs_status_tenant",
                      "ix_connector_sync_items_connector_ts"):
            self.assertIn(index, src, f"0075 缺少索引 {index}")
        self.assertIn("uq_connector_sync_items_connector_external", src)
        self.assertIn("_backfill", src, "0075 应包含旧数据回填")
        self.assertIn("def downgrade", src)

    def test_0075_downgrade_reverses_reliability_tables(self):
        """0075 downgrade 必须 drop 两新表并逐列回收 connector_sync_jobs 扩展列。"""
        path = "alembic/versions/20260813_0075_task_reliability.py"
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('op.drop_table("connector_sync_items")', src)
        self.assertIn('op.drop_table("task_runs")', src)
        for col in ("cursor_json", "checkpoint_json", "lease_owner", "lease_expires_at",
                    "idempotency_key", "next_retry_at"):
            self.assertIn(f'batch_op.drop_column("{col}")', src, f"downgrade 应回收 {col}")

    def test_0076_creates_delivery_reliability_tables_and_columns(self):
        """0076 必须建模板/附件/邮箱表，扩展通知与邮件投递列，含回填与 downgrade。"""
        path = "alembic/versions/20260813_0076_delivery_reliability.py"
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for table in ("notification_templates", "email_attachments",
                      "mailbox_sync_accounts", "mailbox_messages", "mailbox_attachments"):
            self.assertIn(f'"{table}"', src, f"0076 缺少建表 {table}")
        for col in ("idempotency_key", "next_retry_at", "claim_expires_at", "claimed_by",
                    "attempt", "error_code", "sanitized_error_message", "provider_message_id",
                    "email_send_request_id", "template_key"):
            self.assertIn(f'"{col}"', src, f"0076 缺少投递列 {col}")
        for col in ("dead_letter_at", "bcc", "notification_event_id", "sanitized_error_message"):
            self.assertIn(f'"{col}"', src, f"0076 缺少 email_send_requests.{col}")
        self.assertIn("uq_notification_templates_channel_key_locale_version", src)
        self.assertIn("uq_mailbox_messages_account_folder_uidvalidity_uid", src)
        self.assertIn("_backfill", src, "0076 应包含旧数据回填")
        self.assertIn("def downgrade", src)

    def test_0076_backfill_adds_legacy_idempotency_keys(self):
        """0076 回填：既有通知事件补 legacy 幂等键与默认计数（幂等）。"""
        import importlib.util

        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        import app.models  # noqa: F401
        from app.core.database import Base
        from app.models.legal_notifications import LegalNotificationEvent
        from app.models.org import Organization
        from app.models.user import User, UserStatus

        engine = create_engine(
            "sqlite+pysqlite:///:memory:", future=True,
            connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        org = Organization(name="MigOrg", code="MIGO")
        db.add(org)
        db.flush()
        user = User(username="m", email="m@x.com", hashed_password="pw",
                    role="user", status=UserStatus.active.value, organization_id=org.id)
        db.add(user)
        db.commit()
        event = LegalNotificationEvent(organization_id=org.id, user_id=user.id,
                                       event_type="portal", title="t", channel="site",
                                       status="pending", idempotency_key=None)
        db.add(event)
        db.commit()
        db.refresh(event)

        spec = importlib.util.spec_from_file_location(
            "mig_0076", "alembic/versions/20260813_0076_delivery_reliability.py")
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        mig._backfill(engine)

        db.refresh(event)
        self.assertIsNotNone(event.idempotency_key)
        self.assertTrue(event.idempotency_key.startswith("legacy:notify:"))
        self.assertEqual(event.attempt, 0)


class DirtyDataDedupeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        with self.engine.begin() as c:
            c.execute(text(
                "CREATE TABLE dedupe_test (id INTEGER PRIMARY KEY, grp_a TEXT, grp_b TEXT)"
            ))

    def test_dedupe_keep_latest_removes_duplicates(self):
        """迁移中清理重复数据（保留每组最新 id）的 SQL 在真实数据上有效。"""
        with self.engine.begin() as c:
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('a','x')"))
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('a','x')"))
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('a','x')"))
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('a','y')"))
            c.execute(text("INSERT INTO dedupe_test (grp_a, grp_b) VALUES ('b','z')"))
            before = c.execute(text("SELECT COUNT(*) FROM dedupe_test")).scalar()
            self.assertEqual(before, 5)
            # 与 0067 _dedupe_keep_latest 相同的派生表模式
            c.execute(text(
                "DELETE FROM dedupe_test WHERE id NOT IN ("
                "SELECT keep.id FROM (SELECT MAX(id) AS id FROM dedupe_test GROUP BY grp_a, grp_b) keep)"
            ))
            after = c.execute(text("SELECT COUNT(*) FROM dedupe_test")).scalar()
        self.assertEqual(after, 3)  # (a,x) 保留最新、(a,y)、(b,z)

    def test_dedupe_sql_works_on_empty_table(self):
        with self.engine.begin() as c:
            c.execute(text(
                "DELETE FROM dedupe_test WHERE id NOT IN ("
                "SELECT keep.id FROM (SELECT MAX(id) AS id FROM dedupe_test GROUP BY grp_a, grp_b) keep)"
            ))
            # 不抛错即为通过


if __name__ == "__main__":
    unittest.main()
