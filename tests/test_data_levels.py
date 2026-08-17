"""P0 统一数据分级模型测试：枚举、排序、解析与 action 默认分级（deny-by-default）。"""
import unittest

from app.core.data_levels import (
    DataLevel,
    base_level_for_action,
    level_rank,
    max_level,
    parse_level,
)


class DataLevelTests(unittest.TestCase):
    def test_level_ordering_is_public_lt_internal_lt_sensitive_lt_highly_sensitive(self):
        self.assertLess(level_rank(DataLevel.PUBLIC), level_rank(DataLevel.INTERNAL))
        self.assertLess(level_rank(DataLevel.INTERNAL), level_rank(DataLevel.SENSITIVE))
        self.assertLess(level_rank(DataLevel.SENSITIVE), level_rank(DataLevel.HIGHLY_SENSITIVE))

    def test_parse_level_accepts_lowercase_and_rejects_unknown(self):
        self.assertIs(parse_level("public"), DataLevel.PUBLIC)
        self.assertIs(parse_level("HIGHLY_SENSITIVE"), DataLevel.HIGHLY_SENSITIVE)
        self.assertIsNone(parse_level("top_secret"))
        self.assertIsNone(parse_level(None))
        self.assertIsNone(parse_level(""))

    def test_max_level_picks_highest_and_ignores_none(self):
        self.assertIs(max_level(DataLevel.PUBLIC, DataLevel.SENSITIVE), DataLevel.SENSITIVE)
        self.assertIs(
            max_level(DataLevel.INTERNAL, DataLevel.HIGHLY_SENSITIVE, DataLevel.PUBLIC),
            DataLevel.HIGHLY_SENSITIVE,
        )
        self.assertIs(max_level(DataLevel.PUBLIC, None), DataLevel.PUBLIC)
        self.assertIsNone(max_level(None, None))

    def test_public_actions_map_to_public(self):
        self.assertIs(base_level_for_action("embedding"), DataLevel.PUBLIC)
        self.assertIs(base_level_for_action("EMBEDDING"), DataLevel.PUBLIC)

    def test_generic_chat_rag_actions_map_to_internal(self):
        for action in ("chat", "chat_stream", "generate", "generate_with_images", "rag_answer", "rag_query_rewrite"):
            self.assertIs(base_level_for_action(action), DataLevel.INTERNAL, action)

    def test_business_sensitive_prefixes_map_to_sensitive(self):
        for action in (
            "legal_consultation",
            "legal_contract_review",
            "document_summary",
            "meeting_summary",
            "email_generate",
            "task_decompose",
            "agent_plan",
        ):
            self.assertIs(base_level_for_action(action), DataLevel.SENSITIVE, action)

    def test_unknown_action_deny_by_default_to_sensitive(self):
        self.assertIs(base_level_for_action("brand_new_action"), DataLevel.SENSITIVE)
        self.assertIs(base_level_for_action(""), DataLevel.SENSITIVE)
        self.assertIs(base_level_for_action(None), DataLevel.SENSITIVE)


if __name__ == "__main__":
    unittest.main()
