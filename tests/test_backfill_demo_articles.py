"""演示法源语料回填脚本：标题归一化与语料目录匹配。

回填靠「法源标题归一后比对演示目录」来认法源，归一规则一旦松紧失当就会
把不同法源认成同一部法（或漏掉历史遗留的同名法源），所以单独锁住。
"""
import unittest

from app.services.legal.legal_service import DEMO_SOURCES
from scripts.backfill_demo_articles import demo_corpus_catalog, normalize_title


class NormalizeTitleTests(unittest.TestCase):
    def test_article_suffix_and_brackets_are_ignored(self):
        self.assertEqual(
            normalize_title("《中华人民共和国劳动合同法》第40、46、47条（演示依据）"),
            "中华人民共和国劳动合同法",
        )
        self.assertEqual(normalize_title("《中华人民共和国劳动合同法》"), "中华人民共和国劳动合同法")
        self.assertEqual(normalize_title("中华人民共和国劳动合同法（演示数据）"), "中华人民共和国劳动合同法")

    def test_different_laws_do_not_collide(self):
        self.assertNotEqual(
            normalize_title("《中华人民共和国劳动争议调解仲裁法》第27条（演示依据）"),
            normalize_title("《中华人民共和国劳动合同法》第40、46、47条（演示依据）"),
        )

    def test_catalog_covers_every_demo_corpus(self):
        catalog = demo_corpus_catalog()
        self.assertEqual(
            {entry["file"] for entry in catalog.values()},
            {item["corpus"] for item in DEMO_SOURCES if item.get("corpus")},
        )

    def test_catalog_title_normalizes_to_its_key(self):
        for key, found in demo_corpus_catalog().items():
            self.assertEqual(normalize_title(found["entry"]["title"]), key)


if __name__ == "__main__":
    unittest.main()
