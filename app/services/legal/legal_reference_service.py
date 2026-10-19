"""法条引用核验与版本效力（AI-4）。

对 AI 答案中的每一条法源引用做确定性、DB 派生的核验：
  - 效力状态：现行有效 / 待更新 / 已废止
  - 版本效力：是否被后续法规修订（amended_by_json 关联），引用时需核对现行版本
  - 条文命中：引用里的条号是否能在该法源的条文级数据（legal_articles）里找到
核验结果不信任 LLM 输出，一律基于法源库当前数据计算，可离线确定性测试。
"""

from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from app.models.legal import LegalArticle, LegalSource

# 效力状态展示文案
_STATUS_NOTES = {
    "active": "现行有效",
    "pending_update": "待更新，引用时需人工复核",
    "inactive": "已废止，不得作为法律依据",
}
_UNVERIFIED_NOTE = "未关联法源库条目，无法核验"

# 引用条号解析：兼容「第40条」「第40、46、47条」「第40条、第46条」「第四十七条」等写法
_ARTICLE_GROUP = re.compile(r"第([一二三四五六七八九十百零〇\d]+(?:[、,，][一二三四五六七八九十百零〇\d]+)*)条")
_CHINESE_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}


def _chinese_to_int(text: str) -> int | None:
    """中文数字转阿拉伯数字（「四十七」→47、「一百二十」→120）；无法解析返回 None。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    number = 0
    for char in text:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
        elif char in _CHINESE_UNITS:
            total += (number or 1) * _CHINESE_UNITS[char]
            number = 0
        else:
            return None
    return total + number


def _canonical_article_number(raw: str | None) -> str | None:
    """把条号统一成阿拉伯写法（库里存「第47条」，模型爱写「第四十七条」）。"""
    if not raw:
        return None
    match = _ARTICLE_GROUP.search(raw)
    numeral = (match.group(1) if match else raw).strip()
    if re.search(r"[、,，]", numeral):
        return None
    value = _chinese_to_int(numeral)
    return f"第{value}条" if value is not None else None


def parse_citation_numbers(text: str | None) -> list[str]:
    """把引用文本解析成规范化条号列表，如「第40、46、47条」→ ['第40条','第46条','第47条']。"""
    numbers: list[str] = []
    for group in _ARTICLE_GROUP.findall(text or ""):
        for part in re.split(r"[、,，]", group):
            canonical = _canonical_article_number(part)
            if canonical:
                numbers.append(canonical)
    return list(dict.fromkeys(numbers))


def _article_verification(
    db: Session | None, source: LegalSource, citation: str | None
) -> tuple[bool | None, str, str]:
    """按条号核对法源条文，返回 (是否命中, 说明, 可核验性口径)。

    三级如实：该法源没有条文级数据 / 引用没写条号 → None（面板显示「未校验」，不硬判）；
    条号全部命中 → True；有缺失 → False 并点名缺失条号。

    第三个返回值是给「在线证据可核验性」用的机器可读口径，取值：
    `verified`（逐条核对通过）/ `missing`（有条文库但引用条号没找到）/
    `no_corpus`（该法源没有条文级数据）/ `no_numbers`（引用没写条号）/ `no_db`（无库环境）。
    """
    if db is None:
        return None, "无数据库连接，未做条文级核对", "no_db"
    existing = {
        canonical
        for (raw,) in db.query(LegalArticle.article_number).filter(LegalArticle.source_id == source.id).all()
        if (canonical := _canonical_article_number(raw or ""))
    }
    if not existing:
        return None, "该法源未收录条文级数据，未做逐条核对", "no_corpus"
    numbers = parse_citation_numbers(citation)
    if not numbers:
        return None, "引用未标注条号，未做逐条核对", "no_numbers"
    missing = [number for number in numbers if number not in existing]
    if missing:
        # 已收录条数是硬事实：让「本条未收录」与「引用可能有误」在文案上可区分，避免误判成引用错误。
        return (
            False,
            (
                f"引用的{'、'.join(missing)}未在已收录的 {len(existing)} 条条文中找到"
                "（可能是该条未收录，也可能引用有误）"
            ),
            "missing",
        )
    return True, f"引用的{'、'.join(numbers)}已与法源条文核对一致", "verified"


def _json_ids(raw: str | None) -> list[int]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [int(item) for item in value if str(item).isdigit()]


def verify_source(source: LegalSource, db: Session | None = None, *, citation: str | None = None) -> dict:
    """对单个法源计算引用核验信息（版本 + 效力状态 + 修订链 + 条文命中）。

    db 为 None 时无法解析修订方的标题，仅按是否存在修订关系标记 superseded。
    citation 为模型给出的引用文本（如「第40、46、47条」），用于条文级核对。
    """
    amended_ids = _json_ids(source.amended_by_json)

    amended_by: list[dict] = []
    if amended_ids and db is not None:
        rows = (
            db.query(LegalSource)
            .filter(LegalSource.id.in_(amended_ids))
            .all()
        )
        by_id = {row.id: row for row in rows}
        for rid in amended_ids:
            row = by_id.get(rid)
            if row:
                amended_by.append({
                    "source_id": row.id,
                    "title": row.title,
                    "version": row.version,
                    "status": row.status,
                    "effective_date": str(row.effective_date) if row.effective_date else None,
                })

    superseded = bool(amended_by) or bool(amended_ids and db is None)
    active = source.status == "active"
    current_effective = active and not superseded

    # AI-4 收尾：被修订/废止时推荐现行有效版本（修订链中 status=active 的第一项）
    recommended = None
    if superseded:
        for item in amended_by:
            if item["status"] == "active":
                recommended = {
                    "source_id": item["source_id"],
                    "title": item["title"],
                    "version": item["version"],
                    "effective_date": item["effective_date"],
                }
                break

    if source.status == "inactive":
        note = _STATUS_NOTES["inactive"]
    elif source.status == "pending_update":
        note = _STATUS_NOTES["pending_update"]
    elif amended_by:
        titles = "、".join(f"《{item['title']}》" for item in amended_by[:3])
        note = f"已被{titles}修订，引用时注意核对现行版本"
    elif amended_ids:
        note = "已被后续法规修订，引用时注意核对现行版本"
    else:
        note = _STATUS_NOTES["active"]

    verified, basis, article_scope = _article_verification(db, source, citation)

    return {
        "source_id": source.id,
        "version": source.version,
        "status": source.status,
        "effective_date": str(source.effective_date) if source.effective_date else None,
        "current_effective": current_effective,
        "superseded": superseded,
        "amended_by": amended_by,
        "recommended_source": recommended,
        "verification_note": note,
        "verified": verified,
        "verification_basis": basis,
        "article_scope": article_scope,
    }


def enrich_references(
    db: Session | None,
    refs: list[dict],
    *,
    sentence_numbers: dict[int, list[str]] | None = None,
) -> list[dict]:
    """为引用列表批量附加 verification（DB 派生，覆盖任何 LLM 输出）。

    db 为 None（无库环境，如评测/单测）时不附加 verification，原样返回。
    sentence_numbers 是「按引用下标给出、从答案正文里收集到的条号」，只用于条级核对，
    不改写引用自身的 citation 文本。
    """
    if not refs or db is None:
        return refs
    ids = [ref.get("source_id") for ref in refs if ref.get("source_id")]
    by_id: dict[int, LegalSource] = {}
    if ids:
        rows = db.query(LegalSource).filter(LegalSource.id.in_(ids)).all()
        by_id = {row.id: row for row in rows}

    enriched = []
    for index, ref in enumerate(refs):
        out = dict(ref)
        source = by_id.get(ref.get("source_id")) if ref.get("source_id") else None
        if source is None:
            out["verification"] = {
                "verified": False,
                "verification_note": _UNVERIFIED_NOTE,
                "article_scope": "no_source",
            }
        else:
            extra = " ".join((sentence_numbers or {}).get(index) or [])
            citation = " ".join(part for part in (ref.get("citation"), extra) if part)
            out["verification"] = verify_source(source, db=db, citation=citation)
        enriched.append(out)
    return enriched


_EVIDENCE_LEVELS = {
    "strong": "引用已逐条核对",
    "partial": "部分引用已核对",
    "weak": "引用待核",
    "none": "未引用法源",
}


def evidence_summary(refs: list[dict], *, materials: int = 0) -> dict:
    """把一次答复的证据可核验性汇总成在线指标（随响应返回，前端据此提示用户）。

    口径只用「已发生的事实」：条文级核对做过没有、通过没有、引用有没有挂到法源库。
    不做加权评分，也不把「未收录条文」说成「引用错误」。
    """
    counts = {"verified": 0, "missing": 0, "no_corpus": 0, "no_numbers": 0, "no_source": 0}
    for ref in refs or []:
        verification = ref.get("verification") if isinstance(ref, dict) else None
        scope = (verification or {}).get("article_scope") if isinstance(verification, dict) else None
        counts[scope if scope in counts else "no_source"] += 1

    total = sum(counts.values())
    if total == 0:
        level = "none"
    elif counts["verified"] == total:
        level = "strong"
    elif counts["verified"]:
        level = "partial"
    else:
        level = "weak"

    notes: list[str] = []
    if total == 0:
        notes.append("本次答复没有引用法源，无法据此核验事实断言")
    if counts["verified"]:
        notes.append(f"{counts['verified']} 条引用已与法源条文核对一致")
    if counts["missing"]:
        notes.append(f"{counts['missing']} 条引用的条号未在已收录条文中找到")
    if counts["no_corpus"]:
        notes.append(f"{counts['no_corpus']} 条引用所在法源没有条文级数据")
    if counts["no_numbers"]:
        notes.append(f"{counts['no_numbers']} 条引用未标注条号，未做逐条核对")
    if counts["no_source"]:
        notes.append(f"{counts['no_source']} 条引用未挂到法源库")
    if materials:
        notes.append(f"已把 {materials} 份案件材料纳入证据")

    return {
        "version": 1,
        "level": level,
        "label": _EVIDENCE_LEVELS[level],
        "references": total,
        "article_checked": counts["verified"] + counts["missing"],
        "article_verified": counts["verified"],
        "article_missing": counts["missing"],
        "no_corpus": counts["no_corpus"],
        "no_numbers": counts["no_numbers"],
        "unmatched": counts["no_source"],
        "materials": int(materials or 0),
        "note": "；".join(notes),
    }


def _as_date(value) -> "object":
    """把 date/datetime 统一为 date 用于范围比较；None 原样返回。"""
    if value is None:
        return None
    if hasattr(value, "date"):
        return value.date()
    return value


def check_applicability(
    source: LegalSource,
    analysis_date=None,
    jurisdiction: str | None = None,
) -> dict:
    """法源适用性判定：状态 + 生效/失效日期 + 地域。

    规则（P1）：
      1. status=inactive → 不适用；status=pending_update → 待核验（applicable=None）。
      2. analysis_date < effective_date → 尚未生效，不适用。
      3. analysis_date > expiration_date → 已失效/废止，不适用。
      4. 目标地域与法源 jurisdiction 不一致 → 地域不匹配（applicable=None + 限制说明）。
      5. 以上均不命中且 status=active → 适用。

    返回 {"applicable": True|False|None, "reason": str}。
    applicable=None 表示适用性未知或待核验，不得据此发布确定法律结论。
    """
    effective_date = _as_date(source.effective_date)
    expiration_date = _as_date(source.expiration_date)
    analysis_date = _as_date(analysis_date) if analysis_date is not None else None

    if source.status == "inactive":
        return {"applicable": False, "reason": "已废止，不得作为法律依据"}
    if source.status == "pending_update":
        return {"applicable": None, "reason": "法源待更新，适用性需人工复核"}

    if analysis_date is not None:
        if effective_date is not None and analysis_date < effective_date:
            return {"applicable": False, "reason": f"分析时点早于生效日期（{effective_date.isoformat()}）"}
        if expiration_date is not None and analysis_date > expiration_date:
            return {"applicable": False, "reason": f"分析时点晚于失效日期（{expiration_date.isoformat()}）"}

    if jurisdiction and source.jurisdiction and source.jurisdiction != jurisdiction:
        return {
            "applicable": None,
            "reason": f"地域不匹配：目标{jurisdiction}，法源为{source.jurisdiction}",
        }

    if source.status == "active":
        return {"applicable": True, "reason": "现行有效且适用"}
    return {"applicable": None, "reason": "法源状态未知，适用性待核验"}
