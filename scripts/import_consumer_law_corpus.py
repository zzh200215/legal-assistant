"""从中国人大网抓取《消费者权益保护法》2013 年修正文本，重建条文语料。

为什么单独写脚本：法源语料的准确性靠「官方来源 + 条号/章节可核对」，不能靠记忆录入。
本脚本把抓取、分段、条号校验和写盘放在同一条可重跑路径上；`--check` 只校验不落盘。

用法：
    python -B scripts/import_consumer_law_corpus.py
    python -B scripts/import_consumer_law_corpus.py --check
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx
import lxml.html

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "scripts" / "legal_corpus" / "消费者权益保护法.json"
URL = "http://www.npc.gov.cn/npc/c2/c12435/c12488/201905/t20190521_184603.html"
EXPECTED_ARTICLES = 63

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def cn2int(text: str) -> int:
    text = text.strip()
    if text == "十":
        return 10
    if "百" in text:
        left, _, rest = text.partition("百")
        hundreds = CN_DIGITS.get(left, 1) * 100 if left else 100
        return hundreds + (cn2int(rest) if rest else 0)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = CN_DIGITS.get(left, 1) if left else 1
        ones = CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return CN_DIGITS[text]


def fetch_articles() -> list[dict]:
    response = httpx.get(URL, timeout=40, follow_redirects=True)
    response.raise_for_status()
    tree = lxml.html.fromstring(response.text)
    paragraphs = [re.sub(r"\s+", " ", item.text_content()).strip() for item in tree.xpath("//p")]
    paragraphs = [item for item in paragraphs if item]

    chapter_re = re.compile(r"^第[一二三四五六七八九十]+章\s*")
    article_re = re.compile(r"^第([一二三四五六七八九十百零]+)条\s*(.*)$")

    articles: list[dict] = []
    chapter = ""
    current: dict | None = None
    started = False
    for paragraph in paragraphs:
        chapter_match = chapter_re.match(paragraph)
        if chapter_match and len(paragraph) < 30:
            if current:
                articles.append(current)
                current = None
            prefix, _, title = paragraph.partition("章")
            chapter = f"{prefix}章 {title.replace(' ', '')}".strip()
            continue
        if not started:
            if article_re.match(paragraph):
                started = True
            else:
                continue
        match = article_re.match(paragraph)
        if match:
            if current:
                articles.append(current)
            number = cn2int(match.group(1))
            current = {
                "article_number": f"第{number}条",
                "content": match.group(2).strip(),
                "chapter": chapter,
                "sequence": number,
            }
        elif current is not None:
            current["content"] = (current["content"] + "\n" + paragraph).strip()
    if current:
        articles.append(current)
    return articles


def validate(articles: list[dict]) -> None:
    if len(articles) != EXPECTED_ARTICLES:
        raise ValueError(f"条数不符：期望 {EXPECTED_ARTICLES}，实际 {len(articles)}")
    if [item["sequence"] for item in articles] != list(range(1, EXPECTED_ARTICLES + 1)):
        raise ValueError("条号不是 1..63 连续")
    by_number = {item["article_number"]: item["content"] for item in articles}
    checks = {
        "第1条": "为保护消费者的合法权益",
        "第25条": "七日内退货",
        "第26条": "格式条款",
        "第39条": "（五）向人民法院提起诉讼",
        "第55条": "三倍",
        "第63条": "1994年1月1日",
    }
    for number, needle in checks.items():
        if needle not in by_number.get(number, ""):
            raise ValueError(f"{number} 缺少关键内容：{needle}")
    if any(not item["content"].strip() for item in articles):
        raise ValueError("存在正文为空的条文")


def build_payload(articles: list[dict]) -> dict:
    return {
        "title": "《中华人民共和国消费者权益保护法》",
        "source_type": "statute",
        "document_number": "主席令第7号",
        "promulgator": "全国人民代表大会常务委员会",
        "promulgation_date": "2013-10-25",
        "effective_date": "2014-03-15",
        "jurisdiction": "中国大陆",
        "citation": "消费者权益保护法",
        "version": "2013年修正版",
        "status": "active",
        "law_areas": ["consumer"],
        "keywords": ["消费者", "经营者", "知情权", "退货", "七天无理由退货", "欺诈", "三倍赔偿", "安全保障", "格式条款"],
        "content": "《消费者权益保护法》保护消费者在购买、使用商品或接受服务过程中的合法权益，规范经营者行为，明确消费者权利和经营者义务。本语料为 2013 年修正文本全文，共 63 条；1994 年 1 月 1 日起施行，2014 年 3 月 15 日修正后施行。",
        "articles": articles,
        "provenance": {
            "source": "中国人大网（全国人民代表大会常务委员会）",
            "url": URL,
            "retrieved_at": "2026-10-20",
            "note": "按官方页面逐条录入，保留条号与章节；原文中的全角空格与分段已规范化为普通空格/换行。",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取并校验消费者权益保护法全文语料")
    parser.add_argument("--check", action="store_true", help="只抓取校验，不覆盖现有 JSON")
    parser.add_argument("--output", type=Path, default=OUT, help="输出路径（默认 scripts/legal_corpus/消费者权益保护法.json）")
    args = parser.parse_args()

    articles = fetch_articles()
    validate(articles)
    payload = build_payload(articles)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    if args.check:
        current = json.loads(OUT.read_text(encoding="utf-8"))
        if current.get("articles") != articles:
            print("不一致：现有 JSON 与官方页面解析结果不同", file=sys.stderr)
            return 1
        print(f"一致：{len(articles)} 条，{OUT}")
        return 0

    args.output.write_text(text, encoding="utf-8")
    print(f"写入 {args.output}：{len(articles)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
