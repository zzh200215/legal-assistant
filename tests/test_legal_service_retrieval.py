import unittest
from datetime import date

from app.models.legal import LegalSource
from app.services.legal.legal_service import (
    _extract_citations,
    _rank_sources_by_relevance,
    _tokenize,
    classify_question,
    test_retrieval,
)


def _make_source(id_, title, citation="", content="", status="active", source_type="statute"):
    source = LegalSource(
        id=id_,
        user_id=1,
        title=title,
        source_type=source_type,
        citation=citation,
        jurisdiction="中国大陆",
        effective_date=date.today(),
        version="v1",
        status=status,
        content=content,
    )
    return source


class CitationExtractionTests(unittest.TestCase):
    def test_extracts_book_title_and_article_number(self):
        text = "根据《劳动合同法》第40条，用人单位应当支付经济补偿。"
        citations = _extract_citations(text)
        self.assertIn("《劳动合同法》", citations)
        self.assertIn("第40条", citations)

    def test_no_citation_returns_empty_set(self):
        self.assertEqual(_extract_citations("这段话没有任何法律引用"), set())

    def test_handles_none_and_empty_string(self):
        self.assertEqual(_extract_citations(""), set())
        self.assertEqual(_extract_citations(None), set())


class TokenizeTests(unittest.TestCase):
    """回归防护：原实现用贪婪正则把整句当一个 token，导致关键词匹配完全失真。"""

    def test_splits_sentence_into_meaningful_words(self):
        tokens = _tokenize("公司违法解除劳动合同，需要支付赔偿金吗？")
        self.assertIn("违法", tokens)
        self.assertIn("解除", tokens)
        self.assertIn("劳动合同", tokens)
        self.assertIn("赔偿金", tokens)
        # 不应把整句当成单个 token
        self.assertNotIn("公司违法解除劳动合同需要支付赔偿金吗", tokens)

    def test_filters_stopwords_and_single_chars(self):
        tokens = _tokenize("这是什么意思呢")
        self.assertNotIn("这", tokens)
        self.assertNotIn("是", tokens)
        self.assertNotIn("什么", tokens)

    def test_extracts_english_words(self):
        tokens = _tokenize("民间借贷利率超过LPR四倍怎么办")
        self.assertIn("lpr", tokens)


class ClassifyQuestionTests(unittest.TestCase):
    def test_labor_dispute_classification(self):
        self.assertEqual(classify_question("公司拖欠工资并违法辞退我"), "labor_dispute")

    def test_private_lending_classification(self):
        self.assertEqual(classify_question("我借给朋友的借款一直没有还款"), "private_lending")

    def test_unknown_defaults_to_other(self):
        self.assertEqual(classify_question("今天天气怎么样"), "other")


class HybridRetrievalRankingTests(unittest.TestCase):
    """FL.md 8.2: 混合检索（精确 + 语义）+ 重排序"""

    def setUp(self):
        self.labor_source = _make_source(
            1, "《劳动合同法》第40、46、47条", citation="劳动合同法第40、46、47条",
            content="无过失性辞退需提前30日通知或额外支付一个月工资，经济补偿按工作年限计算。",
        )
        self.lending_source = _make_source(
            2, "民间借贷司法解释", citation="民间借贷司法解释",
            content="民间借贷利率上限、举证责任及合同效力的一般规则。",
        )
        self.inactive_source = _make_source(
            3, "已失效的劳动法规", citation="劳动合同法第40条（失效）",
            content="无过失性辞退相关规定（已失效版本）。", status="inactive",
        )

    def test_exact_citation_match_ranks_first(self):
        question = "根据《劳动合同法》第40条，公司应如何处理辞退？"
        ranked = _rank_sources_by_relevance(question, [self.labor_source, self.lending_source])
        self.assertGreater(len(ranked), 0)
        self.assertEqual(ranked[0].id, self.labor_source.id)

    def test_keyword_match_ranks_relevant_source_higher(self):
        question = "公司未提前通知就辞退我，是否需要支付经济补偿？"
        ranked = _rank_sources_by_relevance(question, [self.labor_source, self.lending_source])
        self.assertEqual(ranked[0].id, self.labor_source.id)

    def test_inactive_source_penalized_below_active(self):
        question = "无过失性辞退是否需要提前通知？"
        ranked = _rank_sources_by_relevance(question, [self.inactive_source, self.labor_source])
        active_idx = [s.id for s in ranked].index(self.labor_source.id)
        if self.inactive_source.id in [s.id for s in ranked]:
            inactive_idx = [s.id for s in ranked].index(self.inactive_source.id)
            self.assertLess(active_idx, inactive_idx)

    def test_no_relevant_source_returns_empty_or_fallback(self):
        question = "今天天气如何"
        ranked = _rank_sources_by_relevance(question, [self.labor_source, self.lending_source])
        # No strong match: either empty or fallback list, but must not raise
        self.assertIsInstance(ranked, list)

    def test_empty_sources_returns_empty_list(self):
        self.assertEqual(_rank_sources_by_relevance("任意问题", []), [])


class RetrievalTestToolTests(unittest.TestCase):
    """FL.md 6.1: 检索测试工具返回评分明细"""

    def setUp(self):
        self.source = _make_source(
            1, "《劳动合同法》第40条", citation="劳动合同法第40条",
            content="无过失性辞退需提前30日通知或额外支付一个月工资。",
        )

    def test_returns_score_breakdown_fields(self):
        results = test_retrieval("公司辞退我未提前通知", [self.source])
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertIn("total_score", item)
        self.assertIn("score_breakdown", item)
        for key in ("citation_match", "keyword_match", "category_match", "query_coverage", "status_weight"):
            self.assertIn(key, item["score_breakdown"])

    def test_results_sorted_by_total_score_descending(self):
        other = _make_source(2, "民间借贷司法解释", citation="民间借贷", content="借款利率规则")
        results = test_retrieval("公司辞退未提前通知劳动合同", [self.source, other])
        scores = [r["total_score"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_empty_sources_returns_empty_results(self):
        self.assertEqual(test_retrieval("任意问题", []), [])


if __name__ == "__main__":
    unittest.main()
