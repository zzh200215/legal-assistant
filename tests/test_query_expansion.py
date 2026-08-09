"""RAG 查询改写（Query Expansion）：法律术语同义扩展。"""
import unittest
from unittest.mock import patch

from app.services.query_expansion import expand_terms
from app.services.rag_service import rag_service


class QueryExpansionTests(unittest.TestCase):
    def test_expand_terms_returns_related_legal_terms(self):
        expanded = expand_terms("员工工资怎么计算")
        self.assertIn("劳动报酬", expanded)
        self.assertIn("薪资", expanded)

    def test_expand_terms_multiple_hits(self):
        expanded = expand_terms("合同解除赔偿")
        self.assertTrue(expanded)
        self.assertIn("违约金", expanded)

    def test_expand_terms_no_hit_returns_empty(self):
        self.assertEqual(expand_terms("今天天气不错"), [])

    def test_expand_terms_excludes_query_contained_words(self):
        expanded = expand_terms("劳动报酬怎么算")
        self.assertNotIn("劳动报酬", expanded)  # query 已含，不重复

    def test_rewrite_queries_appends_expanded_variant_when_enabled(self):
        with patch("app.services.rag_service.settings.RAG_QUERY_EXPANSION_ENABLED", True):
            variants = rag_service._rewrite_queries("员工工资怎么算")
        self.assertTrue(any("工资" in v and ("劳动报酬" in v or "薪资" in v) for v in variants))

    def test_rewrite_queries_no_expansion_when_disabled(self):
        with patch("app.services.rag_service.settings.RAG_QUERY_EXPANSION_ENABLED", False):
            variants = rag_service._rewrite_queries("员工工资怎么算")
        self.assertFalse(any("劳动报酬" in v for v in variants))


if __name__ == "__main__":
    unittest.main()
