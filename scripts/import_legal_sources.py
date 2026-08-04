"""
法源导入脚本 — 用于将结构化法规数据写入 LegalSource + LegalArticle。

用法：
    python scripts/import_legal_sources.py --data scripts/legal_corpus/劳动合同法.json [--user-id 1]

输入格式：
    scripts/legal_corpus/*.json — 每个文件一部法规，格式见下方示例。
    首批覆盖六大领域：劳动、合同、借贷、消费、公司、知识产权，约 100 部。
"""
import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.legal import LegalSource, LegalArticle
from app.models.user import User

# ── 领域映射：标签英文→中文 ────────────
AREA_LABELS = {
    "labor": "劳动法", "contract": "合同法", "lending": "民间借贷",
    "consumer": "消费维权", "company": "公司法", "ip": "知识产权",
    "civil_procedure": "民事诉讼法", "administrative": "行政诉讼",
    "criminal": "刑事", "property": "物权法", "tort": "侵权责任法",
}

ALLOWED_AREAS = set(AREA_LABELS.keys())


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def build_articles_from_full_text(full_text: str, source_id: int) -> list[dict]:
    """自动尝试从法规全文切分条文。

    通过识别"第X条"或"Article X"模式将全文分割为条文片段。
    失败时（无法识别任何条文）返回空列表。
    """
    import re
    articles = []
    pattern = re.compile(r"第[一二三四五六七八九十百零\d]+条")
    parts = pattern.split(full_text)
    matches = pattern.findall(full_text)

    if len(parts) != len(matches) + 1 or len(matches) == 0:
        return []

    for i, (article_label, body) in enumerate(zip(matches, parts[1:]), start=1):
        body = body.strip()
        if len(body) < 10:
            continue
        article_number = article_label.strip()
        # 尝试从正文首句提取标题
        title = None
        first_line = body.split("\n")[0].strip()
        if first_line and len(first_line) < 40 and "。" not in first_line:
            title = first_line
            body = "\n".join(body.split("\n")[1:]).strip()

        articles.append({
            "article_number": article_number,
            "title": title,
            "content": body,
            "sequence": i,
        })

    return articles


def _resolve_relation_ids(refs: list[str], user_id: int, db: Session) -> list[int] | None:
    """将 document_number 或 title 引用列表解析为数据库中的 source_id 列表。

    返回 None 表示输入为空列表（调用方应跳过写入，保留原值）；
    返回 [] 表示显式传入了空列表（调用方应清空原值）；
    返回 [id, ...] 表示解析到的 source_id。
    """
    if refs is None:
        return None
    ids = []
    for ref in refs:
        ref = str(ref).strip()
        if not ref:
            continue
        found = (
            db.query(LegalSource)
            .filter(LegalSource.user_id == user_id)
            .filter(
                (LegalSource.document_number == ref) | (LegalSource.title == ref)
            )
            .first()
        )
        if found:
            ids.append(found.id)
    return ids


def import_source_from_dict(data: dict, user_id: int, db: Session) -> dict:
    """导入一部法规及其条文。返回导入统计。"""
    title = data.get("title", "").strip()
    if not title:
        return {"status": "skipped", "reason": "title required", "title": ""}

    document_number = data.get("document_number", "").strip() or None
    promulgator = data.get("promulgator", "").strip() or None
    full_text = data.get("full_text", "").strip() or None
    content = data.get("content", "").strip() or full_text[:500] if full_text else ""
    law_areas = [a for a in data.get("law_areas", []) if a in ALLOWED_AREAS]
    keywords = data.get("keywords", [])

    # 去重：按 title + document_number 查找
    query = db.query(LegalSource).filter(LegalSource.title == title, LegalSource.user_id == user_id)
    if document_number:
        query = query.filter(LegalSource.document_number == document_number)
    existing = query.first()

    if existing:
        # 更新已有记录
        existing.source_type = data.get("source_type", existing.source_type)
        existing.citation = data.get("citation", existing.citation)
        existing.jurisdiction = data.get("jurisdiction", existing.jurisdiction)
        existing.content = content
        existing.full_text = full_text or existing.full_text
        existing.document_number = document_number or existing.document_number
        existing.promulgator = promulgator or existing.promulgator
        existing.promulgation_date = parse_date(data.get("promulgation_date")) or existing.promulgation_date
        existing.effective_date = parse_date(data.get("effective_date")) or existing.effective_date
        existing.law_area_json = json.dumps(law_areas, ensure_ascii=False) if law_areas else None
        existing.keywords_json = json.dumps(keywords, ensure_ascii=False) if keywords else None
        existing.status = data.get("status", existing.status)
        existing.version = str(data.get("version", existing.version or "v1"))
        existing.updated_at = datetime.utcnow()
        source_id = existing.id
        # 删除旧条文，重新导入
        db.query(LegalArticle).filter(LegalArticle.source_id == source_id).delete()
        action = "updated"
    else:
        source = LegalSource(
            user_id=user_id,
            title=title,
            source_type=data.get("source_type", "statute"),
            citation=data.get("citation", "").strip() or None,
            jurisdiction=data.get("jurisdiction", "中国大陆"),
            document_number=document_number,
            promulgator=promulgator,
            promulgation_date=parse_date(data.get("promulgation_date")),
            effective_date=parse_date(data.get("effective_date")),
            content=content,
            full_text=full_text,
            law_area_json=json.dumps(law_areas, ensure_ascii=False) if law_areas else None,
            keywords_json=json.dumps(keywords, ensure_ascii=False) if keywords else None,
            status=data.get("status", "active"),
            version=str(data.get("version", "v1")),
        )
        db.add(source)
        db.flush()
        source_id = source.id
        action = "created"

    # 导入条文：优先使用显式传入的 articles，否则从 full_text 自动拆分
    article_data = data.get("articles")
    if not article_data and full_text:
        article_data = build_articles_from_full_text(full_text, source_id)

    articles_imported = 0
    if article_data:
        for i, art in enumerate(article_data):
            db.add(LegalArticle(
                source_id=source_id,
                article_number=art.get("article_number", f"第{i+1}条"),
                title=art.get("title"),
                content=art.get("content", ""),
                chapter=art.get("chapter"),
                section=art.get("section"),
                sequence=i + 1,
            ))
            articles_imported += 1

    if articles_imported:
        db.flush()

    # 解析修订关系：将 document_number 或 title 解析为 source_id
    amended_by_ids = _resolve_relation_ids(data.get("amended_by", []), user_id, db)
    amends_ids = _resolve_relation_ids(data.get("amends", []), user_id, db)

    target = existing if action == "updated" else db.query(LegalSource).filter(LegalSource.id == source_id).first()
    if target:
        if amended_by_ids is not None:
            target.amended_by_json = json.dumps(amended_by_ids, ensure_ascii=False)
        if amends_ids is not None:
            target.amends_json = json.dumps(amends_ids, ensure_ascii=False)
        if amended_by_ids is not None or amends_ids is not None:
            db.flush()

    return {
        "status": "success",
        "title": title,
        "action": action,
        "source_id": source_id,
        "articles_imported": articles_imported,
        "relations": {
            "amended_by": amended_by_ids or [],
            "amends": amends_ids or [],
        },
    }


def main():
    parser = argparse.ArgumentParser(description="导入结构化法规数据")
    parser.add_argument("--data", type=str, required=True, help="JSON 文件或目录路径")
    parser.add_argument("--user-id", type=int, default=1, help="法源所有者 user_id")
    args = parser.parse_args()

    data_path = Path(args.data)
    if data_path.is_dir():
        files = sorted(data_path.glob("*.json"))
    else:
        files = [data_path]

    if not files:
        print("未找到 JSON 文件")
        sys.exit(1)

    db = SessionLocal()
    try:
        total = {"created": 0, "updated": 0, "skipped": 0, "articles": 0}
        for fp in files:
            with fp.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    result = import_source_from_dict(item, args.user_id, db)
                    _accumulate(total, result)
            else:
                result = import_source_from_dict(data, args.user_id, db)
                _accumulate(total, result)
            db.commit()

        print(
            f"导入完成: 创建 {total['created']} / 更新 {total['updated']} "
            f"法规，导入 {total['articles']} 条条文"
        )
    except Exception as exc:
        db.rollback()
        print(f"导入失败: {exc}")
        raise
    finally:
        db.close()


def _accumulate(total: dict, result: dict):
    if result["status"] == "success":
        if result["action"] == "created":
            total["created"] += 1
        else:
            total["updated"] += 1
        total["articles"] += result.get("articles_imported", 0)
    else:
        total["skipped"] += 1


if __name__ == "__main__":
    main()
