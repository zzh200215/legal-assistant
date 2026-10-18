"""逐句溯源：建议正文与引用的句级映射（attribute_sentences_to_references）

口径：只用词面重合度（jieba 分词交集 / 本句词数），不调用模型；匹配不上的句子如实留空，
不把整篇引用塞给每一句。这里锁住三件事：能匹配的匹配上、匹配不上的留空、形状与序列化稳定。
"""
import asyncio
import json
import unittest
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import LegalConsultation, LegalSource
from app.services.legal.legal_service import (
    _dedupe_references,
    attribute_sentences_to_references,
    consultation_payload,
    match_reference_to_source,
    split_answer_sentences,
)
from app.services.legal.legal_workspace_service import serialize_workspace_row

LABOR_TEXT = (
    "经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。"
    "六个月以上不满一年的，按一年计算；不满六个月的，向劳动者支付半个月工资的经济补偿。"
)
ARBITRATION_TEXT = (
    "劳动争议申请仲裁的时效期间为一年。仲裁时效期间从当事人知道或者应当知道其权利被侵害之日起计算。"
)
ADVICE = (
    "公司违法解除劳动合同的，应当支付经济补偿，标准按工作年限每满一年支付一个月工资。\n"
    "申请劳动仲裁的时效期间为一年，从知道权利被侵害之日起计算。\n"
    "建议尽快整理证据材料。"
)


class SplitAnswerSentencesTests(unittest.TestCase):
    def test_splits_on_sentence_punctuation_and_newlines(self):
        self.assertEqual(
            split_answer_sentences("第一句。第二句！第三句？\n第四句；第五句"),
            ["第一句。", "第二句！", "第三句？", "第四句；", "第五句"],
        )

    def test_non_string_and_blank_input_return_empty(self):
        self.assertEqual(split_answer_sentences(None), [])
        self.assertEqual(split_answer_sentences("   \n  "), [])


class AttributeSentencesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()

        self.labor = LegalSource(
            user_id=1, title="中华人民共和国劳动合同法", citation="劳动合同法第四十七条",
            source_type="statute", status="active", version="2012年修订版",
            effective_date=date(2008, 1, 1), content=LABOR_TEXT,
        )
        self.arbitration = LegalSource(
            user_id=1, title="中华人民共和国劳动争议调解仲裁法", citation="劳动争议调解仲裁法第二十七条",
            source_type="statute", status="active", version="2008年版",
            content=ARBITRATION_TEXT,
        )
        self.db.add_all([self.labor, self.arbitration])
        self.db.commit()
        self.db.refresh(self.labor)
        self.db.refresh(self.arbitration)
        self.sources = [self.labor, self.arbitration]
        self.refs = [
            {"source_id": self.labor.id, "title": "中华人民共和国劳动合同法", "citation": "第四十七条"},
            {"source_id": self.arbitration.id, "title": "中华人民共和国劳动争议调解仲裁法", "citation": "第二十七条"},
        ]

    def tearDown(self):
        self.db.close()

    def test_sentence_matches_the_source_whose_text_it_overlaps(self):
        result = attribute_sentences_to_references(ADVICE, self.refs, self.sources)
        self.assertEqual(result["granularity"], "sentence")
        self.assertEqual(result["attribution_version"], 1)
        self.assertEqual(result["method"], "lexical_overlap")
        self.assertEqual(result["total_sentences"], 3)

        first, second, third = result["sentences"]
        self.assertEqual([item["index"] for item in result["sentences"]], [0, 1, 2])
        # 第 1 句讲经济补偿/工作年限 -> 劳动合同法；第 2 句讲仲裁时效 -> 调解仲裁法
        self.assertEqual(first["matches"][0]["reference_index"], 0)
        self.assertEqual(first["matches"][0]["source_id"], self.labor.id)
        self.assertGreaterEqual(first["matches"][0]["score"], 0.34)
        self.assertTrue(first["matches"][0]["shared_terms"])
        self.assertEqual(second["matches"][0]["reference_index"], 1)
        self.assertEqual(second["matches"][0]["source_id"], self.arbitration.id)
        # 第 3 句「建议尽快整理证据材料」在两条法源正文里都没有对应词 -> 如实留空
        self.assertEqual(third["matches"], [])
        self.assertEqual(result["matched_sentences"], 2)

    def test_sentence_text_is_preserved_verbatim(self):
        result = attribute_sentences_to_references(ADVICE, self.refs, self.sources)
        self.assertEqual(
            [item["text"] for item in result["sentences"]],
            ["公司违法解除劳动合同的，应当支付经济补偿，标准按工作年限每满一年支付一个月工资。",
             "申请劳动仲裁的时效期间为一年，从知道权利被侵害之日起计算。",
             "建议尽快整理证据材料。"],
        )

    def test_reference_without_source_id_falls_back_to_its_own_title(self):
        """没有 source_id（模型幻觉引用）时用引用自身标题/条号比对，匹配不上就留空。"""
        refs = [{"title": "某不存在的法第99条", "citation": "第九十九条"}]
        result = attribute_sentences_to_references(ADVICE, refs, self.sources)
        self.assertEqual([item["matches"] for item in result["sentences"]], [[], [], []])
        self.assertEqual(result["matched_sentences"], 0)

    def test_reference_without_source_id_can_still_match_by_title(self):
        refs = [{"title": "中华人民共和国劳动合同法", "citation": "第四十七条"}]
        result = attribute_sentences_to_references(
            "劳动合同法第四十七条规定的经济补偿标准是什么？", refs, self.sources,
        )
        matches = result["sentences"][0]["matches"]
        self.assertEqual(len(matches), 1)
        self.assertIsNone(matches[0]["source_id"])

    def test_matches_sorted_by_score_and_capped(self):
        # 两句都同时与两条法源有重合：劳动合同法那条约会胜出，且每句最多 2 条
        advice = "经济补偿按工作年限每满一年支付一个月工资，仲裁时效期间为一年。"
        result = attribute_sentences_to_references(advice, self.refs, self.sources, max_matches=1)
        self.assertEqual(len(result["sentences"][0]["matches"]), 1)

        result_all = attribute_sentences_to_references(advice, self.refs, self.sources)
        scores = [item["score"] for item in result_all["sentences"][0]["matches"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_returns_empty_dict_when_nothing_to_map(self):
        self.assertEqual(attribute_sentences_to_references("", self.refs, self.sources), {})
        self.assertEqual(attribute_sentences_to_references(ADVICE, [], self.sources), {})
        self.assertEqual(attribute_sentences_to_references(ADVICE, [None, "x"], self.sources), {})
        # 引用与法源都没有可比对文本 -> 不编造映射
        self.assertEqual(
            attribute_sentences_to_references(ADVICE, [{"source_id": 999}], self.sources), {},
        )


class ConsultationPayloadAttributionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = Session()
        self.labor = LegalSource(
            user_id=1, title="中华人民共和国劳动合同法", citation="劳动合同法第四十七条",
            source_type="statute", status="active", version="2012年修订版", content=LABOR_TEXT,
        )
        self.db.add(self.labor)
        self.db.commit()
        self.db.refresh(self.labor)
        self.sources = [self.labor]

    def tearDown(self):
        self.db.close()

    def _payload(self, attribution_out=None, trace_out=None):
        async def _no_llm(*_args, **_kwargs):
            return None

        with patch("app.services.legal.legal_service._llm_chat", new=_no_llm):
            return asyncio.run(consultation_payload(
                "公司无故辞退我，劳动合同法第40条怎么适用？", self.sources,
                user_id=1, db=self.db, trace_out=trace_out, attribution_out=attribution_out,
            ))

    def test_attribution_out_is_filled_with_sentence_mapping(self):
        attribution = {}
        _, _, _, refs, advice, _, _ = self._payload(attribution_out=attribution)
        self.assertEqual(attribution["granularity"], "sentence")
        self.assertEqual(attribution["total_sentences"], len(split_answer_sentences(advice)))
        self.assertEqual(len(attribution["sentences"]), attribution["total_sentences"])
        self.assertEqual(attribution["sentences"][0]["index"], 0)
        for item in attribution["sentences"]:
            self.assertIn("text", item)
            self.assertIsInstance(item["matches"], list)
            for match in item["matches"]:
                self.assertLess(match["reference_index"], len(refs))
                self.assertGreaterEqual(match["score"], attribution["min_score"])

    def test_without_attribution_out_behaviour_is_unchanged(self):
        result = self._payload()
        self.assertEqual(len(result), 7)


class ConsultationAttributionSerializationTests(unittest.TestCase):
    def _row(self, **fields):
        row = LegalConsultation(
            user_id=1, case_id=None, question="问题", category="labor", advice="建议。",
            **fields,
        )
        return row

    def test_serialize_returns_attribution_object(self):
        payload = {"granularity": "sentence", "sentences": [{"index": 0, "text": "建议。", "matches": []}]}
        row = self._row(attribution_json=json.dumps(payload, ensure_ascii=False))
        self.assertEqual(serialize_workspace_row(row)["attribution"], payload)

    def test_serialize_returns_empty_object_for_legacy_rows(self):
        self.assertEqual(serialize_workspace_row(self._row())["attribution"], {})

    def test_serialize_falls_back_on_broken_json(self):
        row = self._row(attribution_json="{not json")
        self.assertEqual(serialize_workspace_row(row)["attribution"], {})


class MatchReferenceToSourceTests(unittest.TestCase):
    """模型常只给书名与条号（无 source_id），匹配必须容错，但不能跨法误配。"""

    def _source(self, **fields):
        base = dict(
            user_id=1, title="劳动合同法", source_type="statute", status="active",
            content=LABOR_TEXT, citation="第47条", version="2012年修订版",
        )
        base.update(fields)
        return LegalSource(**base)

    def test_exact_normalized_title_matches(self):
        source = self._source(title="中华人民共和国劳动合同法")
        matched = match_reference_to_source({"title": "《劳动合同法》"}, [source])
        self.assertIs(matched, source)

    def test_contained_title_matches(self):
        source = self._source(title="中华人民共和国劳动争议调解仲裁法", citation="第27条")
        matched = match_reference_to_source({"title": "劳动争议调解仲裁法"}, [source])
        self.assertIs(matched, source)

    def test_citation_numbers_match_with_title_affinity(self):
        source = self._source(title="中华人民共和国劳动合同法", citation="第40条、第46条、第47条")
        matched = match_reference_to_source(
            {"title": "劳动合同法", "citation": "第40、46、47条"}, [source]
        )
        self.assertIs(matched, source)

    def test_citation_numbers_do_not_match_across_laws(self):
        labor = self._source(title="中华人民共和国劳动合同法", citation="第40条")
        arbitration = self._source(title="中华人民共和国劳动争议调解仲裁法", citation="第27条")
        matched = match_reference_to_source(
            {"title": "中华人民共和国劳动争议调解仲裁法", "citation": "第27条"}, [labor, arbitration]
        )
        self.assertIs(matched, arbitration)

    def test_unrelated_reference_returns_none(self):
        source = self._source()
        self.assertIsNone(match_reference_to_source({"title": "中华人民共和国刑法"}, [source]))

    def test_empty_inputs_return_none(self):
        self.assertIsNone(match_reference_to_source({"title": "劳动合同法"}, []))
        self.assertIsNone(match_reference_to_source({"title": ""}, [self._source()]))


class DedupeReferencesTests(unittest.TestCase):
    """同一部法源 + 同一条号只保留一次；不同条号或不同法源不合并。"""

    def test_identical_references_collapse(self):
        refs = [
            {"source_id": 72, "title": "《中华人民共和国劳动合同法》", "citation": "劳动合同法"},
            {"source_id": 72, "title": "中华人民共和国劳动合同法", "citation": "劳动合同法"},
            {"source_id": 72, "title": "中华人民共和国劳动合同法", "citation": "劳动合同法第47条"},
            {"source_id": 25, "title": "中华人民共和国劳动争议调解仲裁法", "citation": "劳动争议调解仲裁法"},
        ]
        unique = _dedupe_references(refs)
        self.assertEqual(len(unique), 3)
        self.assertEqual(unique[0]["source_id"], 72)
        self.assertEqual(unique[1]["citation"], "劳动合同法第47条")
        self.assertEqual(unique[2]["source_id"], 25)

    def test_dedupe_keeps_first_occurrence_and_empty_input(self):
        self.assertEqual(_dedupe_references([]), [])
        first = {"title": "劳动合同法", "citation": "第47条", "source_id": None}
        second = {"title": "劳动合同法", "citation": "第47条", "source_id": None, "verification": {"verified": True}}
        unique = _dedupe_references([first, second])
        self.assertEqual(len(unique), 1)
        self.assertIs(unique[0], first)


if __name__ == "__main__":
    unittest.main()
