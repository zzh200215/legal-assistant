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
    _load_demo_articles,
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

    def test_second_call_is_noop(self):
        ensure_demo_sources(self.db, 102)
        first_sources = self.db.query(LegalSource).count()
        first_articles = self.db.query(LegalArticle).count()
        ensure_demo_sources(self.db, 102)
        self.assertEqual(self.db.query(LegalSource).count(), first_sources)
        self.assertEqual(self.db.query(LegalArticle).count(), first_articles)


if __name__ == "__main__":
    unittest.main()
