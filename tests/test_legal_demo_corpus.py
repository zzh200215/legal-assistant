"""演示法源条文语料：语料文件完整性 + ensure_demo_sources 写入条文

背景：演示法源过去只写一段摘要文字，没有条文级数据，导致引用核验只能停在法源级
（引用条目没有 verification 字段）。本用例锁住「演示库自带条文」这一行为。
"""
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.legal import LegalArticle, LegalSource
from app.services.legal.legal_service import (
    DEMO_SOURCES,
    _compose_corpus_full_text,
    _load_demo_articles,
    _load_demo_corpus,
    ensure_demo_sources,
)

CORPUS_DIR = Path(__file__).resolve().parents[1] / "scripts" / "legal_corpus"


class DemoCorpusFileTests(unittest.TestCase):
    def test_every_corpus_reference_exists(self):
        missing = [
            item["corpus"]
            for item in DEMO_SOURCES
            if item.get("corpus") and not (CORPUS_DIR / item["corpus"]).exists()
        ]
        self.assertEqual(missing, [], f"语料文件缺失：{missing}")

    def test_corpus_files_carry_usable_articles(self):
        corpora = [item["corpus"] for item in DEMO_SOURCES if item.get("corpus")]
        self.assertTrue(corpora, "演示法源没有配置任何语料")
        for corpus in corpora:
            articles = _load_demo_articles(corpus)
            self.assertGreater(len(articles), 0, f"{corpus} 没有解析出条文")
            for article in articles:
                self.assertTrue(str(article.get("content") or "").strip(), f"{corpus} 有条文正文为空")
                self.assertTrue(str(article.get("article_number") or "").strip(), f"{corpus} 有条文缺条号")

    def test_missing_corpus_degrades_to_empty(self):
        self.assertEqual(_load_demo_articles("不存在的语料.json"), [])
        self.assertEqual(_compose_corpus_full_text("不存在的语料.json"), "")

    def test_full_text_is_composed_from_articles(self):
        text = _compose_corpus_full_text("劳动合同法.json")
        self.assertIn("第1条", text)
        self.assertIn("第98条", text)
        self.assertIn("第一章 总则", text)
        self.assertGreater(len(text), 5000, "全文太短，像是只拼了摘要")

    def test_consumer_law_corpus_is_complete(self):
        """消保法语料从 8 条节选补到 2013 年修正文本全文 63 条。"""
        payload = _load_demo_corpus("消费者权益保护法.json")
        articles = payload["articles"]
        self.assertEqual(len(articles), 63)
        self.assertEqual([item["sequence"] for item in articles], list(range(1, 64)))
        by_number = {item["article_number"]: item["content"] for item in articles}
        self.assertIn("七日内退货", by_number["第25条"])
        self.assertIn("三倍", by_number["第55条"])
        self.assertIn("1994年1月1日", by_number["第63条"])
        self.assertIn("npc.gov.cn", payload["provenance"]["url"])
        self.assertIn("63 条", payload["content"])

    def test_private_lending_corpus_is_complete(self):
        """民间借贷规定从 8 条节选补到 2020 年第二次修正文本全文 31 条。"""
        payload = _load_demo_corpus("民间借贷规定.json")
        articles = payload["articles"]
        self.assertEqual(len(articles), 31)
        self.assertEqual([item["sequence"] for item in articles], list(range(1, 32)))
        by_number = {item["article_number"]: item["content"] for item in articles}
        self.assertIn("套取金融机构贷款转贷", by_number["第13条"])
        self.assertIn("一年期贷款市场报价利率四倍", by_number["第25条"])
        self.assertIn("2020年8月20日", by_number["第31条"])
        self.assertIn("cicc.court.gov.cn", payload["provenance"]["url"])
        self.assertEqual(payload["version"], "2020年第二次修正")
        self.assertEqual(payload["effective_date"], "2021-01-01")


class EnsureDemoSourcesTests(unittest.TestCase):
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

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_demo_sources_include_articles(self):
        ensure_demo_sources(self.db, 101)
        sources = self.db.query(LegalSource).filter(LegalSource.user_id == 101).all()
        self.assertEqual(len(sources), len(DEMO_SOURCES))
        with_corpus = [item for item in DEMO_SOURCES if item.get("corpus")]
        self.assertTrue(with_corpus, "演示法源没有配置任何语料")
        expected = sum(len(_load_demo_articles(item["corpus"])) for item in with_corpus)
        actual = self.db.query(LegalArticle).count()
        self.assertEqual(actual, expected, "演示库写入的条文数与语料不一致")
        # 带语料的法源要真的挂上条文（无语料的模板法源如「服务合同审查要点摘要」不在此列）
        titles_with_corpus = {item["title"] for item in with_corpus}
        checked = 0
        for source in sources:
            if source.title not in titles_with_corpus:
                continue
            checked += 1
            self.assertGreater(
                self.db.query(LegalArticle).filter(LegalArticle.source_id == source.id).count(),
                0,
                f"法源 {source.id}《{source.title}》没有条文",
            )
        self.assertEqual(checked, len(with_corpus), "带语料的法源没有被全部检查到")
        # 带语料的法源要同时写全文（检索用），否则条文查得到、检索却看不到
        for source in sources:
            if source.title not in titles_with_corpus:
                continue
            self.assertTrue((source.full_text or "").strip(), f"《{source.title}》没有写全文")

    def test_second_call_is_noop(self):
        ensure_demo_sources(self.db, 102)
        first_sources = self.db.query(LegalSource).count()
        first_articles = self.db.query(LegalArticle).count()
        ensure_demo_sources(self.db, 102)
        self.assertEqual(self.db.query(LegalSource).count(), first_sources)
        self.assertEqual(self.db.query(LegalArticle).count(), first_articles)


if __name__ == "__main__":
    unittest.main()
