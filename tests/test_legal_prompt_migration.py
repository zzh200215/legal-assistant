"""AI-3 回归：0055 迁移把存量法律 Prompt 同步为 prompt_service 规范基线。

覆盖：
  - 存量旧 legal_consultation（无 disclaimer/consumer_dispute）被同步为新基线，保留版本历史
  - 已是规范内容的 legal_contract_review 不重复造版本（幂等）
  - legal_followup / legal_contract_compare 缺失时被补建
"""
import importlib.util
import unittest
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.prompt import PromptTemplate, PromptTemplateVersion  # noqa: F401
from app.services.prompt_defaults import DEFAULT_PROMPT_TEMPLATES

LEGAL_NAMES = {
    "legal_consultation",
    "legal_contract_review",
    "legal_draft_generation",
    "legal_followup",
    "legal_contract_compare",
}

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "20260803_0055_legal_prompts_prompt_service.py"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("legal_prompt_migration_0055", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(name: str) -> dict:
    return next(item for item in DEFAULT_PROMPT_TEMPLATES if item["name"] == name)


class LegalPromptMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.module = _load_migration_module()

    def _insert_template(self, name: str, template: str, variables: str):
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO prompt_templates (name, description, variables, active_version_id, rollout_percentage) "
                    "VALUES (:name, 'x', :variables, NULL, 0)"
                ),
                {"name": name, "variables": variables},
            )
            tmpl_id = conn.execute(
                text("SELECT id FROM prompt_templates WHERE name = :name"), {"name": name}
            ).scalar()
            conn.execute(
                text(
                    "INSERT INTO prompt_template_versions (template_id, version, template, is_active, change_note) "
                    "VALUES (:t, 1, :template, 1, 'init')"
                ),
                {"t": tmpl_id, "template": template},
            )
            ver_id = conn.execute(
                text("SELECT id FROM prompt_template_versions WHERE template_id = :t AND version = 1"),
                {"t": tmpl_id},
            ).scalar()
            conn.execute(
                text("UPDATE prompt_templates SET active_version_id = :v WHERE id = :t"),
                {"v": ver_id, "t": tmpl_id},
            )

    def _run_migration(self):
        with self.engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                self.module.upgrade()

    def test_stale_consultation_synced_and_version_history_preserved(self):
        self._insert_template("legal_consultation", "旧模板内容 {question}", "question")
        self._run_migration()

        with self.engine.begin() as conn:
            active = conn.execute(
                text(
                    "SELECT v.template FROM prompt_templates t "
                    "JOIN prompt_template_versions v ON v.id = t.active_version_id "
                    "WHERE t.name = 'legal_consultation'"
                )
            ).scalar()
            self.assertIn("{disclaimer}", active)
            self.assertIn("consumer_dispute", active)
            self.assertNotIn("{{", active)
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM prompt_template_versions "
                    "WHERE template_id = (SELECT id FROM prompt_templates WHERE name = 'legal_consultation')"
                )
            ).scalar()
            self.assertEqual(count, 2, "旧版本应保留在历史中")

    def test_canonical_template_is_idempotent(self):
        canonical = _canonical("legal_contract_review")
        self._insert_template("legal_contract_review", canonical["template"], canonical["variables"])
        self._run_migration()

        with self.engine.begin() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM prompt_template_versions "
                    "WHERE template_id = (SELECT id FROM prompt_templates WHERE name = 'legal_contract_review')"
                )
            ).scalar()
            self.assertEqual(count, 1, "已是规范内容时不应新增版本")

    def test_missing_templates_are_created(self):
        self._run_migration()

        with self.engine.begin() as conn:
            names = {
                row[0]
                for row in conn.execute(text("SELECT name FROM prompt_templates WHERE name LIKE 'legal_%'")).fetchall()
            }
            self.assertTrue(LEGAL_NAMES.issubset(names))

    def test_missing_tables_skip_gracefully(self):
        Base.metadata.drop_all(self.engine)
        self._run_migration()  # 无 prompt 表时直接返回，不报错


if __name__ == "__main__":
    unittest.main()
