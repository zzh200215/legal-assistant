import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.services.prompt_defaults import DEFAULT_PROMPT_TEMPLATES
from app.services.prompt_service import PromptService


class PromptServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()
        self.service = PromptService()
        self.sessionlocal_patcher = patch("app.services.prompt_service.SessionLocal", TestingSessionLocal)
        self.sessionlocal_patcher.start()

    def tearDown(self):
        self.sessionlocal_patcher.stop()
        self.db.close()

    def _find_user_id_for_bucket(self, template_name: str, *, upper_bound: int | None = None, lower_bound: int | None = None) -> int:
        for user_id in range(1, 5000):
            bucket = self.service._stable_bucket(template_name, user_id)
            if upper_bound is not None and bucket < upper_bound:
                return user_id
            if lower_bound is not None and bucket >= lower_bound:
                return user_id
        self.fail("No matching user_id found for rollout bucket test")

    def test_parse_variables_schema_supports_csv_and_json(self):
        csv_schema = self.service._parse_variables_schema("question,context")
        json_schema = self.service._parse_variables_schema(
            json.dumps(
                [
                    {"name": "question", "required": True, "description": "user question"},
                    {"name": "context", "required": False, "description": "retrieved chunks"},
                ],
                ensure_ascii=False,
            )
        )

        self.assertEqual(csv_schema[0]["name"], "question")
        self.assertTrue(csv_schema[0]["required"])
        self.assertEqual(json_schema[1]["name"], "context")
        self.assertFalse(json_schema[1]["required"])

    def test_create_rejects_missing_variable_declaration(self):
        with self.assertRaisesRegex(ValueError, "missing declarations"):
            self.service.create(
                name="bad_prompt",
                template="Question: {question}\nContext: {context}",
                variables="question",
                change_note="init bad",
                db=self.db,
            )

    def test_update_rejects_unused_declared_variable(self):
        tmpl = self.service.create(
            name="good_prompt",
            template="Question: {question}",
            variables="question",
            change_note="init good",
            db=self.db,
        )

        with self.assertRaisesRegex(ValueError, "not used in template"):
            self.service.update(
                tmpl.id,
                self.db,
                variables="question,context",
                change_note="add unused variable",
            )

    def test_default_templates_seed_with_json_examples(self):
        seeded = self.service.seed_defaults(self.db)

        self.assertEqual(seeded, len(DEFAULT_PROMPT_TEMPLATES))
        self.assertIsNotNone(self.service.get_by_name("document_field_extract", self.db))
        self.assertIsNotNone(self.service.get_by_name("agent_supervisor_plan", self.db))

    def test_serialize_template_includes_variables_schema_and_experiment_refs(self):
        tmpl = self.service.create(
            name="rag_answer",
            template="Question: {question}\nContext: {context}",
            variables="question,context",
            change_note="init rag",
            db=self.db,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "experiments": [
                            {
                                "name": "baseline",
                                "effective_config": {
                                    "prompt_template": "rag_answer",
                                    "prompt_version": 1,
                                },
                                "summary": {"hit_at_k": 1.0},
                                "baseline_delta": {"hit_at_k": 0.0},
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch("app.services.prompt_service.EVAL_OUTPUT_SUMMARY_PATH", summary_path):
                payload = self.service.serialize_template(tmpl)

        self.assertEqual(payload["variables_schema"][0]["name"], "question")
        self.assertEqual(payload["versions"][0]["experiment_refs"][0]["experiment_name"], "baseline")

    def test_render_by_name_routes_users_to_rollout_version_stably(self):
        tmpl = self.service.create(
            name="email_generate",
            template="v1 -> {purpose}",
            variables="purpose",
            change_note="init email",
            db=self.db,
        )
        tmpl = self.service.update(
            tmpl.id,
            self.db,
            template="v2 -> {purpose}",
            change_note="second version",
        )
        first_version = next(item for item in tmpl.versions if item.version == 1)
        rollout_user_id = self._find_user_id_for_bucket("email_generate", upper_bound=30)
        stable_user_id = self._find_user_id_for_bucket("email_generate", lower_bound=30)

        second_version = next(item for item in tmpl.versions if item.version == 2)
        self.service.activate_version(tmpl.id, first_version.id, self.db)
        self.service.start_rollout(tmpl.id, second_version.id, 30, self.db)

        rollout_rendered = self.service.render_by_name("email_generate", db=self.db, user_id=rollout_user_id, purpose="周报")
        stable_rendered = self.service.render_by_name("email_generate", db=self.db, user_id=stable_user_id, purpose="周报")
        rollout_metadata = self.service.get_template_metadata("email_generate", user_id=rollout_user_id)
        stable_metadata = self.service.get_template_metadata("email_generate", user_id=stable_user_id)

        self.assertEqual(rollout_rendered, "v2 -> 周报")
        self.assertEqual(stable_rendered, "v1 -> 周报")
        self.assertEqual(rollout_metadata["prompt_version"], 2)
        self.assertTrue(rollout_metadata["is_rollout"])
        self.assertEqual(stable_metadata["prompt_version"], 1)
        self.assertFalse(stable_metadata["is_rollout"])

    def test_rollback_clears_rollout_and_restores_active_version(self):
        tmpl = self.service.create(
            name="meeting_summary",
            template="v1 -> {meeting_content}",
            variables="meeting_content",
            change_note="init meeting",
            db=self.db,
        )
        tmpl = self.service.update(
            tmpl.id,
            self.db,
            template="v2 -> {meeting_content}",
            change_note="second version",
        )
        first_version = next(item for item in tmpl.versions if item.version == 1)
        second_version = next(item for item in tmpl.versions if item.version == 2)
        rollout_user_id = self._find_user_id_for_bucket("meeting_summary", upper_bound=40)

        self.service.activate_version(tmpl.id, first_version.id, self.db)
        self.service.start_rollout(tmpl.id, second_version.id, 40, self.db)
        before_rollback = self.service.render_by_name("meeting_summary", db=self.db, user_id=rollout_user_id, meeting_content="内容")
        rolled_back = self.service.rollback(tmpl.id, self.db)
        after_rollback = self.service.render_by_name("meeting_summary", db=self.db, user_id=rollout_user_id, meeting_content="内容")

        self.assertEqual(before_rollback, "v2 -> 内容")
        self.assertEqual(after_rollback, "v1 -> 内容")
        self.assertIsNone(rolled_back.rollout_version_id)
        self.assertEqual(rolled_back.rollout_percentage, 0)

    def test_activate_version_records_previous_stable_and_supports_rollback(self):
        tmpl = self.service.create(
            name="task_decompose",
            template="v1 -> {title}",
            variables="title",
            change_note="init task",
            db=self.db,
        )
        tmpl = self.service.update(
            tmpl.id,
            self.db,
            template="v2 -> {title}",
            change_note="second version",
        )
        first_version = next(item for item in tmpl.versions if item.version == 1)
        second_version = next(item for item in tmpl.versions if item.version == 2)

        self.service.activate_version(tmpl.id, first_version.id, self.db)
        activated = self.service.activate_version(tmpl.id, second_version.id, self.db)
        activated_version_number = activated.active_version.version
        previous_active_version_number = activated.previous_active_version.version
        restored = self.service.rollback(tmpl.id, self.db)

        self.assertEqual(activated_version_number, 2)
        self.assertEqual(previous_active_version_number, 1)
        self.assertEqual(restored.active_version.version, 1)


if __name__ == "__main__":
    unittest.main()
