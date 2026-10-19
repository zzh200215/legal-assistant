"""把仓库内条文语料回填到已存在的演示法源（只补条文数为 0 的）。

背景：`ensure_demo_sources()` 过去只写一段摘要文字，演示库没有条文级数据，
引用核验只能停在法源级。新环境由 `ensure_demo_sources()` 直接带条文入库；
**已经建过演示库的环境**用本脚本补齐，避免重建用户数据。

    python -B scripts/backfill_demo_articles.py            # 实际写入
    python -B scripts/backfill_demo_articles.py --dry-run  # 只看会写什么

幂等：只处理「标题命中演示法源 + 当前 0 条条文」的法源，重复运行不会重复插入。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.database import SessionLocal  # noqa: E402
from app.models.legal import LegalArticle, LegalSource  # noqa: E402
from app.services.legal.legal_service import DEMO_SOURCES, _load_demo_articles  # noqa: E402


def demo_corpus_by_title() -> dict[str, str]:
    return {
        item["title"]: item["corpus"]
        for item in DEMO_SOURCES
        if item.get("corpus")
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="回填演示法源的条文语料")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    args = parser.parse_args()

    mapping = demo_corpus_by_title()
    db = SessionLocal()
    filled_sources = filled_articles = 0
    per_corpus: Counter[str] = Counter()
    try:
        sources = db.query(LegalSource).order_by(LegalSource.user_id, LegalSource.id).all()
        for source in sources:
            corpus_file = mapping.get(source.title)
            if not corpus_file:
                continue
            existing = db.query(LegalArticle).filter(LegalArticle.source_id == source.id).count()
            if existing:
                continue
            articles = _load_demo_articles(corpus_file)
            if not articles:
                print(f"[skip] source {source.id} 《{source.title}》：语料 {corpus_file} 无条文")
                continue
            print(
                f"[{'dry' if args.dry_run else 'fill'}] source {source.id} (user {source.user_id}) "
                f"《{source.title}》← {corpus_file}：{len(articles)} 条"
            )
            filled_sources += 1
            filled_articles += len(articles)
            per_corpus[corpus_file] += 1
            if args.dry_run:
                continue
            for article in articles:
                db.add(
                    LegalArticle(
                        source_id=source.id,
                        article_number=str(article.get("article_number") or "")[:32] or "第1条",
                        title=(str(article.get("title"))[:256] if article.get("title") else None),
                        content=article["content"],
                        chapter=(str(article.get("chapter"))[:64] if article.get("chapter") else None),
                        section=(str(article.get("section"))[:64] if article.get("section") else None),
                        sequence=int(article.get("sequence") or 0),
                    )
                )
        if not args.dry_run and filled_articles:
            db.commit()
    finally:
        db.close()

    print(
        f"\n{'（dry-run）' if args.dry_run else ''}法源 {filled_sources} 个、条文 {filled_articles} 条"
        f"；按语料：{dict(per_corpus)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
