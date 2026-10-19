"""把仓库内条文语料回填/同步到演示法源。

背景：`ensure_demo_sources()` 过去只写一段摘要文字，演示库没有条文级数据，
引用核验只能停在法源级。新环境由 `ensure_demo_sources()` 直接带条文入库；
**已经建过演示库的环境**用本脚本补齐，避免重建用户数据。

    python -B scripts/backfill_demo_articles.py            # 只补条文数为 0 的法源
    python -B scripts/backfill_demo_articles.py --dry-run  # 只看会写什么
    python -B scripts/backfill_demo_articles.py --sync     # 语料变了就整体替换（含全文/标题对齐）

匹配口径：法源标题去掉《》与括号补充说明后与演示目录标题比对，因此
「《中华人民共和国劳动合同法》第40、46、47条（演示依据）」与「《中华人民共和国劳动合同法》」
视为同一部法，历史遗留的同名法源（早期手工导入）也会一并升级。

幂等：`--sync` 用条文指纹（条号+正文）判断是否已是最新，一致时不动库。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.models.legal import LegalArticle, LegalSource  # noqa: E402
from app.services.legal.legal_service import (  # noqa: E402
    DEMO_SOURCES,
    _compose_corpus_full_text,
    _load_demo_articles,
)

_PAREN = re.compile(r"[（(][^）)]*[）)]")
# 标题里的条号补充（「…第40、46、47条」）不是法名的一部分，归一化时一并去掉
_ARTICLE_TAIL = re.compile(r"第[0-9０-９一二三四五六七八九十百、,，和及至\-~]+条.*$")
_CATALOG_KEYS = ("title", "citation", "jurisdiction", "version", "content", "status")


def normalize_title(title: str) -> str:
    text = _PAREN.sub("", title or "")
    text = _ARTICLE_TAIL.sub("", text)
    for ch in "《》 \u3000":
        text = text.replace(ch, "")
    return text.strip()


def demo_corpus_catalog() -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    for item in DEMO_SOURCES:
        corpus_file = item.get("corpus")
        if not corpus_file:
            continue
        catalog[normalize_title(item["title"])] = {"file": corpus_file, "entry": item}
    return catalog


def _fingerprint(rows) -> str:
    payload = "\n".join(f"{number}|{content}" for number, content in rows)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _article_rows(db, source_id: int):
    rows = (
        db.query(LegalArticle)
        .filter(LegalArticle.source_id == source_id)
        .order_by(LegalArticle.sequence, LegalArticle.id)
        .all()
    )
    return [(row.article_number or "", row.content or "") for row in rows], rows


def _insert_articles(db, source_id: int, articles: list[dict]) -> None:
    for article in articles:
        db.add(
            LegalArticle(
                source_id=source_id,
                article_number=str(article.get("article_number") or "")[:32] or "第1条",
                title=(str(article.get("title"))[:256] if article.get("title") else None),
                content=article["content"],
                chapter=(str(article.get("chapter"))[:64] if article.get("chapter") else None),
                section=(str(article.get("section"))[:64] if article.get("section") else None),
                sequence=int(article.get("sequence") or 0),
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="回填/同步演示法源的条文语料")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    parser.add_argument(
        "--sync",
        action="store_true",
        help="语料与库内条文不一致时整体替换，并对齐标题/引用/全文",
    )
    args = parser.parse_args()

    catalog = demo_corpus_catalog()
    db = SessionLocal()
    filled_sources = filled_articles = synced_sources = 0
    per_corpus: Counter[str] = Counter()
    try:
        sources = db.query(LegalSource).order_by(LegalSource.user_id, LegalSource.id).all()
        for source in sources:
            found = catalog.get(normalize_title(source.title))
            if not found:
                continue
            corpus_file = found["file"]
            entry = found["entry"]
            articles = _load_demo_articles(corpus_file)
            if not articles:
                print(f"[skip] source {source.id} 《{source.title}》：语料 {corpus_file} 无条文")
                continue
            current, rows = _article_rows(db, source.id)
            corpus_print = _fingerprint(
                (str(a.get("article_number") or ""), a["content"]) for a in articles
            )
            stale = bool(rows) and _fingerprint(current) != corpus_print
            full_text = _compose_corpus_full_text(corpus_file)
            meta_keys = [
                key
                for key in _CATALOG_KEYS
                if entry.get(key) is not None
                and getattr(source, key, None) != entry[key]
                # 非演示法源（用户自己导入的）不改名，只升级条文与全文
                and not (key == "title" and "（演示" not in (source.title or ""))
            ]
            text_stale = bool(full_text) and (source.full_text or "") != full_text
            if rows and not stale and not (args.sync and (meta_keys or text_stale)):
                continue
            if rows and not args.sync:
                print(
                    f"[keep] source {source.id} (user {source.user_id}) 《{source.title}》："
                    f"已有 {len(rows)} 条条文，与语料不一致（加 --sync 才替换）"
                )
                continue

            if not rows:
                action = "fill"
            elif stale:
                action = "sync"
            else:
                action = "meta"
            detail = f"{len(articles)} 条" + (f"（替换 {len(rows)} 条）" if stale else "（条文一致）")
            print(
                f"[{'dry' if args.dry_run else action}] source {source.id} (user {source.user_id}) "
                f"《{source.title}》← {corpus_file}：{detail}"
                + (f"；对齐字段 {meta_keys}" if meta_keys else "")
            )
            if stale:
                synced_sources += 1
            elif not rows:
                filled_sources += 1
            filled_articles += len(articles) if stale or not rows else 0
            per_corpus[corpus_file] += 1
            if args.dry_run:
                continue

            if stale:
                for row in rows:
                    db.delete(row)
                db.flush()
                _insert_articles(db, source.id, articles)

            for key in meta_keys:
                setattr(source, key, entry[key])
            if text_stale:
                source.full_text = full_text
            if normalize_title(source.title) != normalize_title(entry["title"]):
                print(f"       ⚠ 标题归一不一致：{source.title!r} vs {entry['title']!r}")
        if not args.dry_run and (filled_articles or synced_sources):
            db.commit()
    finally:
        db.close()

    print(
        f"\n{'（dry-run）' if args.dry_run else ''}补条文法源 {filled_sources} 个、"
        f"替换法源 {synced_sources} 个、条文 {filled_articles} 条；按语料：{dict(per_corpus)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
