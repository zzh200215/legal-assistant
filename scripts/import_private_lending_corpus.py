"""从最高人民法院国际商事法庭（CICC）抓取《民间借贷规定》2020 年第二次修正文本。

为什么单独写脚本：这部司法解释有 2015 年原文、2020-08-18 第一次修正、2020-12-29
第二次修正三个版本；现有语料只节选 8 条，且元数据把「第一次修正的日期」和
「第二次修正的文号」混在一起。脚本把版本号、公布/施行日期、31 条正文固定在
同一条可重跑路径上，并用官方页面的转载版本做逐条交叉校验。

用法：
    python -B scripts/import_private_lending_corpus.py
    python -B scripts/import_private_lending_corpus.py --check
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
OUT = ROOT / "scripts" / "legal_corpus" / "民间借贷规定.json"
CICC = "https://cicc.court.gov.cn/html/1/380/385/12844.html"
CROSS_CHECK = "https://syxfy.jxfy.gov.cn/article/detail/2022/09/id/6906103.shtml"
EXPECTED_ARTICLES = 31

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def cn2int(text: str) -> int:
    text = text.strip()
    if text == "十":
        return 10
    if "十" in text:
        left, _, right = text.partition("十")
        tens = CN_DIGITS.get(left, 1) if left else 1
        ones = CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return CN_DIGITS[text]


def _paragraphs(url: str) -> list[str]:
    response = httpx.get(url, timeout=40, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    tree = lxml.html.fromstring(response.text)
    return [re.sub(r"\s+", " ", item.text_content()).strip() for item in tree.xpath("//p") if item.text_content().strip()]


def split_articles(blob: str) -> list[dict]:
    matches = list(re.finditer(r"(?:^|\s)第([一二三四五六七八九十百零]+)条\s+", blob))
    articles: list[dict] = []
    for index, match in enumerate(matches):
        number = cn2int(match.group(1))
        end = matches[index + 1].start() if index + 1 < len(matches) else len(blob)
        content = re.sub(r"\s+", "\n", blob[match.end() : end]).strip()
        articles.append(
            {"article_number": f"第{number}条", "content": content, "chapter": "", "sequence": number}
        )
    return articles


def fetch_articles() -> list[dict]:
    paragraphs = _paragraphs(CICC)
    candidates = [item for item in paragraphs if "第一条" in item and "第三十一条" in item]
    if not candidates:
        raise ValueError("CICC 页面没有找到 31 条正文")
    return split_articles(max(candidates, key=len))


def validate(articles: list[dict]) -> None:
    if len(articles) != EXPECTED_ARTICLES:
        raise ValueError(f"条数不符：期望 {EXPECTED_ARTICLES}，实际 {len(articles)}")
    if [item["sequence"] for item in articles] != list(range(1, EXPECTED_ARTICLES + 1)):
        raise ValueError("条号不是 1..31 连续")
    by_number = {item["article_number"]: item["content"] for item in articles}
    checks = {
        "第1条": "资金融通",
        "第9条": "视为合同成立",
        "第13条": "套取金融机构贷款转贷",
        "第25条": "一年期贷款市场报价利率四倍",
        "第31条": "2020年8月20日",
    }
    for number, needle in checks.items():
        if needle not in by_number.get(number, ""):
            raise ValueError(f"{number} 缺少关键内容：{needle}")
    if any(not item["content"].strip() for item in articles):
        raise ValueError("存在正文为空的条文")

    # 与转载版本逐条比对（只比正文，忽略空白；第31条转载页带站点页脚，单独放行）
    cross = split_articles(" ".join(_paragraphs(CROSS_CHECK)))
    if len(cross) != EXPECTED_ARTICLES:
        raise ValueError(f"交叉校验页条数不符：{len(cross)}")
    norm = lambda text: re.sub(r"\s+", "", text)
    mismatched = [
        official["article_number"]
        for official, repost in zip(articles, cross)
        if official["article_number"] != "第31条"
        and norm(official["content"]) != norm(repost["content"])
    ]
    if mismatched:
        raise ValueError(f"交叉校验不一致：{mismatched}")


def build_payload(articles: list[dict]) -> dict:
    return {
        "title": "最高人民法院关于审理民间借贷案件适用法律若干问题的规定",
        "source_type": "judicial_interpretation",
        "document_number": "法释〔2020〕17号",
        "promulgator": "最高人民法院",
        "promulgation_date": "2020-12-29",
        "effective_date": "2021-01-01",
        "jurisdiction": "中国大陆",
        "citation": "民间借贷司法解释",
        "version": "2020年第二次修正",
        "status": "active",
        "law_areas": ["lending"],
        "keywords": ["民间借贷", "利率", "利息", "转贷", "举证责任", "虚假诉讼", "网络借贷", "保证", "合同效力", "LPR"],
        "content": "民间借贷利率上限、举证责任及合同效力的一般规则。本语料为 2020 年第二次修正文本全文，共 31 条。",
        "articles": articles,
        "provenance": {
            "source": "最高人民法院国际商事法庭（CICC）",
            "url": CICC,
            "retrieved_at": "2026-10-20",
            "cross_check": CROSS_CHECK,
            "note": "按官方页面全文逐条拆分；与上犹县法院转载文本逐条比对，第1–30条一致（第31条转载页含站点页脚，以官方页面为准）。",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取并校验民间借贷规定全文语料")
    parser.add_argument("--check", action="store_true", help="只抓取校验，不覆盖现有 JSON")
    parser.add_argument("--output", type=Path, default=OUT, help="输出路径（默认 scripts/legal_corpus/民间借贷规定.json）")
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
