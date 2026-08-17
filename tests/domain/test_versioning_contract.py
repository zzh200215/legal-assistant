"""Domain 纯逻辑：版本化契约（If-Match 解析 / ETag 生成）边界测试。

覆盖 app/core/versioning.py 的 parse_if_match / etag_for：
- 引号 / 无引号 / 空白变体、非法值、None 的解析契约；
- ETag 生成格式与「解析 ↔ 生成」往返。
契约口径与 app/main.py `_IF_MATCH_HEADER` 声明一致：If-Match 形如 ``"v{n}"``。
"""

import unittest

from app.core.versioning import etag_for, parse_if_match


class ParseIfMatchContractTests(unittest.TestCase):
    def test_unquoted_v_token_parses(self):
        self.assertEqual(parse_if_match("v3"), 3)

    def test_quoted_v_token_parses(self):
        self.assertEqual(parse_if_match('"v3"'), 3)

    def test_whitespace_tolerated(self):
        self.assertEqual(parse_if_match('  "v7"  '), 7)

    def test_none_and_empty_mean_absent(self):
        self.assertIsNone(parse_if_match(None))
        self.assertIsNone(parse_if_match(""))
        self.assertIsNone(parse_if_match("   "))

    def test_invalid_tokens_mean_absent(self):
        for bad in ("abc", "v", "v3x", "v3.5", '"x1"', "*", "0x10"):
            self.assertIsNone(parse_if_match(bad), bad)

    def test_negative_token_parses_to_negative_version(self):
        """边界文档化：'v-1' 被解析为 -1（而非 None）。

        真实版本从 1 起，-1 永远不匹配任何资源 → 下游恒 409（fail-closed，
        不产生错误成功），故该解析行为安全，予以保留并记录。
        """
        self.assertEqual(parse_if_match("v-1"), -1)

    def test_large_version_parses(self):
        self.assertEqual(parse_if_match("v2147483648"), 2147483648)


class EtagContractTests(unittest.TestCase):
    def test_etag_format_is_quoted_v(self):
        self.assertEqual(etag_for(3), '"v3"')
        self.assertEqual(etag_for(0), '"v0"')

    def test_etag_none_means_v0(self):
        self.assertEqual(etag_for(None), '"v0"')

    def test_parse_generate_roundtrip(self):
        for version in (0, 1, 42, 999):
            self.assertEqual(parse_if_match(etag_for(version)), version)


if __name__ == "__main__":
    unittest.main()
