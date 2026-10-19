"""Legal-workspace service with LLM-powered analysis.

Falls back to deterministic keyword logic when LLM is unavailable.
Generated text is always framed as AI-assisted and missing facts are
surfaced instead of being invented.
"""

import json
import logging
import re
import time
from datetime import date
from pathlib import Path

import jieba
from sqlalchemy.orm import Session

from app.models.legal import (
    ContractReview,
    LegalArticle,
    LegalConsultation,
    LegalDraft,
    LegalSource,
)
from app.services.org.data_protection_service import data_protection_service
from app.services.legal.legal_reference_service import enrich_references, parse_citation_numbers
from app.services.llm.prompt_service import prompt_service

logger = logging.getLogger(__name__)

DISCLAIMER = "AI 辅助结果，不构成正式法律意见；高风险事项请提交审核律师。"

DISCLAIMER_LEVELS = {
    "low": {"level": "low", "label": "仅供参考", "color": "green"},
    "medium": {"level": "medium", "label": "建议律师复核", "color": "yellow"},
    "high": {"level": "high", "label": "必须律师审核", "color": "red"},
}


def compute_disclaimer_level(risk_level: str, category: str = "") -> dict:
    HIGH_RISK_CATEGORIES = {"criminal", "tort", "litigation", "labor_compensation"}
    if risk_level == "high" or category in HIGH_RISK_CATEGORIES:
        return DISCLAIMER_LEVELS["high"]
    if risk_level == "medium":
        return DISCLAIMER_LEVELS["medium"]
    return DISCLAIMER_LEVELS["low"]


NO_VALID_SOURCE = "当前无可验证的有效法源依据，以下分析仅供参考，不得作为确定性法律结论。"

# 拒答：仅当同时命中「实施/求助意图」与「违法/危害对象」才拒答，避免误伤正常法律咨询
# （如「故意伤害他人要承担什么责任」问责任、无实施意图，不拒答；「怎么故意伤害他人」则拒答）。
REFUSAL_ACTION_WORDS = (
    "怎么", "如何", "教我", "帮我", "支招", "办法", "方法", "手段", "步骤",
    "操作", "流程", "计划", "策划", "组织", "指导一下", "怎么做", "如何操作",
)
REFUSAL_TOPIC_WORDS = (
    "洗钱", "洗黑钱", "洗白", "转移赃款", "赃款", "销赃", "隐匿赃物", "受贿", "行贿", "收受贿赂",
    "逃税", "偷税", "虚开发票", "骗保", "骗贷", "骗取贷款", "骗补", "伪造公章",
    "伪造合同", "伪造证件", "伪造发票", "做假账", "造假账", "买凶", "雇凶",
    "投毒", "下毒", "走私", "贩毒", "制毒", "买卖毒品", "碰瓷", "敲诈", "绑架",
    "杀人", "蓄意伤人", "故意伤害他人", "盗窃", "抢劫", "诈骗", "欺诈钱财",
    "制假售假", "卖假药", "贩售假药", "破解", "破解密码", "入侵系统", "攻击网站",
    "网络攻击", "贩卖个人信息", "倒卖个人信息", "人肉搜索", "赌博出千", "套现",
)
REFUSAL_ADVICE = (
    "该问题涉及违法或危害行为，系统不提供任何操作指导或实施建议。"
    "如你或他人正面临法律风险，请立即咨询执业律师或向公安机关求助。"
    f" {DISCLAIMER}"
)


def _should_refuse(question: str) -> bool:
    """Detect requests for guidance on committing illegal or harmful acts."""
    q = question or ""
    has_action = any(word in q for word in REFUSAL_ACTION_WORDS)
    has_topic = any(word in q for word in REFUSAL_TOPIC_WORDS)
    return has_action and has_topic

DEMO_SOURCES = [
    {
        "title": "《中华人民共和国民法典》合同编（演示依据）",
        "source_type": "statute",
        "citation": "民法典合同编",
        "jurisdiction": "中国大陆",
        "version": "现行版本（需复核）",
        "content": "合同订立、履行、违约责任及争议解决的一般规则。此条目用于演示检索链路，正式使用前应核验最新法源。",
        "corpus": "合同法.json",
    },
    {
        "title": "《中华人民共和国劳动合同法》（演示依据）",
        "source_type": "statute",
        "citation": "劳动合同法",
        "jurisdiction": "中国大陆",
        "version": "2012年修订版",
        "content": "劳动合同订立、履行、解除与终止，经济补偿与赔偿金，劳务派遣与非全日制用工及法律责任。语料为 2012 年修正文本全文（98 条）。正式使用前应核验最新法源。",
        "corpus": "劳动合同法.json",
    },
    {
        "title": "《中华人民共和国劳动争议调解仲裁法》（演示依据）",
        "source_type": "statute",
        "citation": "劳动争议调解仲裁法",
        "jurisdiction": "中国大陆",
        "version": "现行版本（需复核）",
        "content": "劳动争议调解与仲裁程序：仲裁时效、申请与受理、开庭与裁决、终局裁决与先予执行。语料为现行文本全文（54 条）。正式使用前应核验最新法源。",
        "corpus": "劳动争议调解仲裁法.json",
    },
    {
        "title": "《最高人民法院关于审理民间借贷案件适用法律若干问题的规定》（演示依据）",
        "source_type": "statute",
        "citation": "民间借贷司法解释",
        "jurisdiction": "中国大陆",
        "version": "现行版本（需复核）",
        "content": "民间借贷利率上限、举证责任及合同效力的一般规则。此条目用于演示检索链路，正式使用前应核验最新法源。",
        "corpus": "民间借贷规定.json",
    },
    {
        "title": "《中华人民共和国消费者权益保护法》（演示依据）",
        "source_type": "statute",
        "citation": "消费者权益保护法",
        "jurisdiction": "中国大陆",
        "version": "现行版本（需复核）",
        "content": "消费者权利、经营者义务、欺诈三倍赔偿与争议解决。语料为节选条文（8 条），覆盖常用主张；正式使用前应核验最新法源。",
        "corpus": "消费者权益保护法.json",
    },
    {
        "title": "服务合同审查要点摘要（演示模板）",
        "source_type": "template",
        "citation": "合同审查模板",
        "jurisdiction": "中国大陆",
        "version": "v1",
        "content": "服务合同审查应关注：付款节点与验收前置条件、交付标准与逾期处理、违约金计算方式与上限、保密范围与期限、知识产权归属、解除条件与通知期限、争议解决机构与管辖地。此条目用于演示检索链路。",
    },
]

# 演示法源里的 `corpus` 指向仓库内的条文语料（`scripts/legal_corpus/*.json`）。
# 演示库过去只有一段摘要文字，没有条文级数据，导致「引用核验」只能停在法源级
# （引用条目没有 verification 字段），条级核验与忠实度判官都拿不到条文正文。
_DEMO_CORPUS_DIR = Path(__file__).resolve().parents[3] / "scripts" / "legal_corpus"


def _load_demo_corpus(corpus_file: str) -> dict:
    """读仓库内语料文件；文件缺失/损坏时返回空字典（演示库退化成只有摘要）。"""
    path = _DEMO_CORPUS_DIR / corpus_file
    if not path.exists():
        logger.warning("legal_service: demo corpus missing: %s", path)
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("legal_service: demo corpus unreadable: %s (%s)", path, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_demo_articles(corpus_file: str) -> list[dict]:
    """语料文件里的条文列表。"""
    articles = _load_demo_corpus(corpus_file).get("articles")
    return [item for item in articles or [] if isinstance(item, dict) and item.get("content")]


def _compose_corpus_full_text(corpus_file: str) -> str:
    """把条文拼成全文（检索用）；语料自带 `full_text` 时优先用它。"""
    payload = _load_demo_corpus(corpus_file)
    if not payload:
        return ""
    text = payload.get("full_text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    parts: list[str] = []
    chapter: str | None = None
    for item in _load_demo_articles(corpus_file):
        current = str(item.get("chapter") or "")
        if current and current != chapter:
            chapter = current
            parts.append(f"\n{current}\n")
        parts.append(f"{item.get('article_number') or ''} {item.get('content')}".strip())
    return "\n".join(parts).strip()

CATEGORY_KEYWORDS = {
    "labor_dispute": ("劳动", "工资", "加班", "辞退", "解除劳动", "社保", "工伤"),
    "contract_dispute": ("合同", "违约", "交付", "付款", "赔偿", "服务协议"),
    "private_lending": ("借款", "借条", "利息", "还款", "民间借贷"),
    "consumer_dispute": ("消费", "退货", "退款", "商品质量", "虚假宣传", "欺诈", "七日无理由"),
}

REQUIRED_FACTS = {
    "labor_dispute": ["劳动关系起止时间", "争议请求及金额", "解除或欠薪证据"],
    "contract_dispute": ["合同签署主体", "关键履行时间线", "违约事实与损失证据"],
    "private_lending": ["借款金额与日期", "还款约定", "转账或借条证据"],
    "consumer_dispute": ["购买商品或服务", "消费金额与日期", "争议事实与诉求"],
    "other": ["当事人身份与所在地", "关键时间线", "现有证据清单"],
}

CLAUSE_RULES = [
    ("payment", ("付款", "支付", "价款", "费用"), "付款条款", "medium", "明确付款节点、验收前置条件和逾期责任。"),
    ("delivery", ("交付", "验收", "服务期限"), "交付与验收", "medium", "补充交付标准、验收方式及逾期处理。"),
    ("breach", ("违约", "违约金"), "违约责任", "high", "核对责任边界、违约金计算方式和损失举证。"),
    ("compensation", ("赔偿", "补偿", "损害赔偿", "损失赔偿"), "赔偿条款", "high", "明确赔偿范围、计算方式、限额和免责情形。"),
    ("confidentiality", ("保密", "秘密"), "保密义务", "medium", "限定保密信息范围、期限、例外和返还义务。"),
    ("ip", ("知识产权", "著作权", "成果归属"), "知识产权", "high", "明确成果归属、许可范围及第三方侵权责任。"),
    ("termination", ("解除", "终止", "提前"), "解除与终止", "high", "补充解除条件、通知期限和交接后果。"),
    ("dispute_resolution", ("争议", "仲裁", "诉讼", "管辖"), "争议解决", "medium", "明确争议解决机构、管辖地及送达方式。"),
]

DRAFT_FIELDS = {
    "labor_arbitration_application": ["申请人", "被申请人", "劳动关系起止时间", "仲裁请求", "事实与理由", "证据清单"],
    "private_lending_complaint": ["原告", "被告", "借款金额", "借款日期", "诉讼请求", "事实与理由", "证据清单"],
    "consumer_complaint": ["投诉人", "被投诉企业", "购买商品或服务", "消费金额与日期", "投诉请求", "事实与理由", "证据清单"],
    "supplementary_agreement": ["甲方", "乙方", "原协议名称", "补充事项", "生效日期", "签署地点"],
}

# FL.md 6.4: 必填字段 — 姓名身份、金额、日期、地址、请求、证据类字段不可留空
DRAFT_REQUIRED_FIELDS = {
    "labor_arbitration_application": ["申请人", "被申请人", "仲裁请求", "事实与理由", "证据清单"],
    "private_lending_complaint": ["原告", "被告", "借款金额", "借款日期", "诉讼请求", "事实与理由", "证据清单"],
    "consumer_complaint": ["投诉人", "被投诉企业", "投诉请求", "事实与理由"],
    "supplementary_agreement": ["甲方", "乙方", "补充事项", "生效日期"],
}

# 法律 Prompt 已迁入 prompt_service 版本化模板（legal_consultation / legal_contract_review /
# legal_draft_generation / legal_followup / legal_contract_compare），按 user_id 灰度。
# 模板内容以 app/services/prompt_defaults.py 为基线，DB 中可版本化替换与 A/B。


def _dump(value):
    return json.dumps(value, ensure_ascii=False)


async def _llm_chat(prompt: str, user_id: int | None = None, action: str = "legal_consultation") -> str | None:
    """Call LLM with fallback. Returns None on failure."""
    try:
        from app.services.llm.llm_service import llm_service
        return await llm_service.generate(
            prompt,
            temperature=0.3,
            action=action,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("LLM call failed, falling back to deterministic logic: %s", exc, exc_info=True)
        return None


def _parse_json_safe(text: str) -> dict | None:
    """Try to parse JSON from LLM output, stripping code fences."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    return None


# ── Public API ────────────────────────────────────────────────────

def test_retrieval(question: str, sources: list[LegalSource]) -> list[dict]:
    """检索测试工具：返回带评分明细的召回结果，用于管理员调试检索效果。

    FL.md 6.1：检索路由——支持管理员输入问题实时测试法源召回效果
    """
    keywords = _tokenize(question)
    category = classify_question(question)
    category_keywords = set(CATEGORY_KEYWORDS.get(category, []))
    keywords |= category_keywords
    citations = _extract_citations(question)

    results = []
    for source in sources:
        body = source.full_text or source.content or ""
        source_text = f"{source.title} {source.citation or ''} {body}"
        source_text_lower = source_text.lower()

        citation_score = sum(10.0 for cite in citations if cite in source_text)
        matched = [kw for kw in keywords if kw in source_text_lower]
        keyword_score = len(matched) * 2.0
        category_hits = sum(1 for kw in category_keywords if kw in source_text_lower)
        category_score = category_hits * 1.5
        coverage = (len(matched) / len(keywords)) if keywords else 0.0
        coverage_score = coverage * 3.0
        status_score = {"active": 1.0, "pending_update": -0.5, "inactive": -3.0}.get(source.status, 0.0)

        total = citation_score + keyword_score + category_score + coverage_score + status_score

        results.append({
            "source_id": source.id,
            "title": source.title,
            "citation": source.citation,
            "status": source.status,
            "total_score": round(total, 2),
            "score_breakdown": {
                "citation_match": round(citation_score, 2),
                "keyword_match": round(keyword_score, 2),
                "category_match": round(category_score, 2),
                "query_coverage": round(coverage_score, 2),
                "status_weight": round(status_score, 2),
            },
            "matched_keywords": matched[:10],
        })

    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results


# This is an administrator-facing retrieval diagnostic, not a pytest test.
# Keep the public name for API compatibility while preventing pytest from
# collecting it when imported into a test module.
test_retrieval.__test__ = False


def ensure_demo_sources(db: Session, user_id: int):
    if db.query(LegalSource).filter(LegalSource.user_id == user_id).count():
        return
    for item in DEMO_SOURCES:
        entry = dict(item)
        corpus_file = entry.pop("corpus", None)
        full_text = _compose_corpus_full_text(corpus_file) if corpus_file else ""
        if full_text:
            entry["full_text"] = full_text
        source = LegalSource(
            user_id=user_id,
            effective_date=date.today(),
            status="active",
            **entry,
        )
        db.add(source)
        db.flush()  # 条文要挂 source.id
        for article in _load_demo_articles(corpus_file) if corpus_file else []:
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
    db.commit()


def classify_question(question: str) -> str:
    for category, words in CATEGORY_KEYWORDS.items():
        if any(word in question for word in words):
            return category
    return "other"


def _redact_for_llm(text: str) -> tuple[str, dict]:
    """Redact PII before sending to LLM; return (redacted_text, inspection)."""
    result = data_protection_service.redact(text)
    inspection = {
        "findings": result.get("findings", []),
        "total_count": result.get("total_count", 0),
        "redacted": result.get("redacted", False),
    }
    if inspection["redacted"]:
        logger.info("legal_service: redacted %d sensitive items before LLM call", inspection["total_count"])
    return result["text"], inspection


CITATION_PATTERN = re.compile(r"《[^》]{2,40}》|第[一二三四五六七八九十百零\d]+条")

# 无实际检索价值的高频虚词/疑问词，分词后需过滤，否则会拉低 query coverage 信号
STOPWORDS = {
    "的", "是", "吗", "了", "呢", "吧", "啊", "我", "你", "他", "她", "它",
    "这", "那", "什么", "怎么", "如何", "可以", "应该", "需要", "还是",
    "以及", "还有", "并且", "但是", "如果", "因为", "所以", "对于", "关于",
    "一个", "一下", "一些", "自己", "没有", "有没有", "多少", "几个",
}


def _extract_citations(text: str) -> set[str]:
    """Extract exact legal citation mentions, e.g. 《劳动合同法》, 第40条."""
    return set(CITATION_PATTERN.findall(text or ""))


# 书名规范化：去掉书名号/空白/括号，以及「中华人民共和国」前缀（模型爱写全称，库里的标题不一定）
_TITLE_NOISE = re.compile(r"[《》\s（）()]")


def _normalize_source_title(title: str) -> str:
    text = _TITLE_NOISE.sub("", title or "")
    return text[len("中华人民共和国"):] if text.startswith("中华人民共和国") else text


def _citation_numbers(text: str) -> set[str]:
    """条号集合（复用 legal_reference_service 的解析，兼容「第40、46、47条」写法）。"""
    return set(parse_citation_numbers(text))


def _title_affinity(left: str, right: str) -> bool:
    """书名相似度（字符集合重叠 ≥3），用于「条号相同但书名不同」时避免跨法误配。"""
    return len(set(left) & set(right)) >= 3


def match_reference_to_source(ref: dict, sources: list[LegalSource]) -> LegalSource | None:
    """把模型返回的引用对上法源库条目。

    模型经常只给书名与条号、不给 source_id（形状 A），此前只用「标题或引用完全相等」匹配，
    真实数据下几乎匹配不上，导致核验只能显示「无法核验」、句级溯源也没有正文可比。
    这里三级放宽：① 规范化书名完全一致 → ② 书名互相包含（≥4 字）→ ③ 条号有交集且书名相似。
    都对不上就返回 None，宁可让该引用保持「无法核验」，也不硬配一条错法源。
    """
    if not sources:
        return None
    ref_title = _normalize_source_title(ref.get("title"))
    if not ref_title:
        return None
    ref_numbers = _citation_numbers(f"{ref.get('citation') or ''} {ref.get('title') or ''}")
    contained = numbered = None
    for source in sources:
        source_title = _normalize_source_title(source.title)
        if not source_title:
            continue
        if source_title == ref_title:
            return source
        if (
            contained is None
            and len(source_title) >= 4 and len(ref_title) >= 4
            and (source_title in ref_title or ref_title in source_title)
        ):
            contained = source
            continue
        if numbered is None and ref_numbers and _title_affinity(ref_title, source_title):
            if ref_numbers & _citation_numbers(source.citation or ""):
                numbered = source
    return contained or numbered


def _tokenize(text: str) -> set[str]:
    """中文分词 + 英文单词提取，过滤停用词和单字噪声。

    原实现用 [\\u4e00-\\u9fff]{2,} 贪婪匹配连续汉字，会把整句当成一个"词"
    （如"公司违法解除劳动合同"被当成单个 token），导致关键词匹配和 query
    coverage 信号完全失真。改用 jieba 分词后才能得到"违法解除""赔偿金"等
    真正可匹配法源正文的短语。
    """
    text_lower = text.lower()
    tokens = set()
    for word in jieba.cut(text_lower):
        word = word.strip()
        if len(word) >= 2 and word not in STOPWORDS and re.search(r"[一-鿿]", word):
            tokens.add(word)
    for word in re.findall(r"[A-Za-z]{3,}", text_lower):
        tokens.add(word)
    return tokens


def _rank_sources_by_relevance(question: str, sources: list[LegalSource], max_rounds: int = 2) -> list[LegalSource]:
    """Hybrid retrieval: exact citation match + keyword/semantic scoring + rerank + supplementary retrieval.

    FL.md 8.2: 问题分类、查询改写、混合检索（精确 + 语义）、重排序、证据评估、有限轮次补检索

    评分策略（重排序信号）：
    1. 精确检索：问题中出现的法规名称/条文编号（如“劳动合同法第40条”）直接命中法源 -> 最高权重
    2. 语义/关键词检索：问题分词与法源标题、引用、正文的关键词重合度
    3. 分类关键词：问题所属分类（劳动争议/合同纠纷等）关联领域词命中
    4. query coverage：法源命中的关键词占问题总关键词的比例，覆盖度越高排序越靠前
    5. 状态加权：当前有效法源优先于待更新/失效法源
    """
    if not sources:
        return []

    keywords = _tokenize(question)
    category = classify_question(question)
    category_keywords = set(CATEGORY_KEYWORDS.get(category, []))
    keywords |= category_keywords
    citations = _extract_citations(question)

    scored = []

    for source in sources:
        source_text = f"{source.title} {source.citation or ''} {source.content or ''}"
        # 如果有 full_text，用全文替换 content 部分（Phase 7-3：条文级检索）
        if source.full_text:
            source_text = f"{source.title} {source.citation or ''} {source.full_text}"
        source_text_lower = source_text.lower()

        relevance_score = 0.0
        matched_keywords = 0
        cite_hits = 0

        for cite in citations:
            if cite in source_text:
                relevance_score += 10.0
                cite_hits += 1

        for kw in keywords:
            if kw in source_text_lower:
                relevance_score += 2.0
                matched_keywords += 1

        category_hits = sum(1 for kw in category_keywords if kw in source_text_lower)
        relevance_score += category_hits * 1.5

        if keywords:
            coverage = matched_keywords / len(keywords)
            relevance_score += coverage * 3.0

        # 状态加权仅在已有实质相关性信号（精确引用/关键词/分类词命中）时才生效，
        # 避免"无关问题 + active 法源"仅凭状态分就被误判为命中（原 bug：
        # score>0 的判定条件里混入了与问题内容无关的状态分，导致拒答场景全部误召回）
        has_relevance_signal = cite_hits > 0 or matched_keywords > 0 or category_hits > 0
        status_score = 0.0
        if has_relevance_signal:
            if source.status == "active":
                status_score = 1.0
            elif source.status == "pending_update":
                status_score = -0.5
            elif source.status == "inactive":
                status_score = -3.0

        total_score = relevance_score + status_score
        scored.append((source, total_score, has_relevance_signal))

    scored.sort(key=lambda x: x[1], reverse=True)
    best = [s for s, sc, has_signal in scored[:5] if has_signal and sc > 0]

    if len(best) == 0 and max_rounds > 1:
        for source, _, _ in scored:
            source_text = f"{source.title} {source.citation or ''}".lower()
            char_matches = sum(1 for c in question if c.strip() and c in source_text)
            if char_matches > 3:
                best.append(source)
                if len(best) >= 5:
                    break

    return best


def _trace_step(step_id: str, label: str, *, status: str = "success", duration_ms: int = 0, detail: str = "") -> dict:
    """过程 trace 的一步：状态 + 耗时 + 人可读依据。

    面板时间线直读这些字段，不再由前端按结果反推，所以 detail 必须写「实际做了什么」，
    没做到的步骤要如实降级（status=degraded/skipped），不能编造。
    """
    return {
        "id": step_id,
        "label": label,
        "status": status,
        "duration_ms": max(0, int(duration_ms)),
        "detail": detail,
    }


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _reference_verification_counts(refs: list) -> tuple[int, int, int]:
    """引用核验三态计数：已核验 / 未核验 / 无核验字段（与前端证据卡的判定口径一致）。"""
    verified = unverified = 0
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        verdict = ref.get("verification")
        if not isinstance(verdict, dict):
            continue
        if verdict.get("verified") is True:
            verified += 1
        elif verdict.get("verified") is False:
            unverified += 1
    return verified, unverified, len([r for r in (refs or []) if isinstance(r, dict)]) - verified - unverified


def _attach_trace_metrics(
    trace_out: dict | None, *, candidate_sources: int, refs: list, counts: tuple[int, int, int],
) -> None:
    """把可机读的计数一并写进 trace。

    面板只读步骤文案，但接口与评测需要能直接取数（命中数 / 引用数 / 核验三态）。
    `rounds` 固定 1：咨询链路是单跳（一次检索 + 一次生成），没有多轮补检，
    写死一个不存在的轮次数才是失真。
    """
    if trace_out is None or not trace_out.get("steps"):
        return
    verified, unverified, unmarked = counts
    trace_out["metrics"] = {
        "rounds": 1,
        "candidate_sources": candidate_sources,
        "references": len([ref for ref in (refs or []) if isinstance(ref, dict)]),
        "verified": verified,
        "unverified": unverified,
        "unmarked": unmarked,
    }


# 逐句溯源：句子与法源正文的词面重合度门槛、每句最多给几条依据
ATTRIBUTION_MIN_SCORE = 0.34
ATTRIBUTION_MAX_MATCHES = 2

# 与前端 splitAnswerSentences 同一口径：句末标点后断开，换行也断开，标点保留在句尾
SENTENCE_PATTERN = re.compile(r"[^。！？；!?;\n]+[。！？；!?;]*")


def split_answer_sentences(text: str) -> list[str]:
    """把建议正文切成句子（与前端 splitAnswerSentences 保持一致，句索引才能对齐）。"""
    if not isinstance(text, str):
        return []
    return [part.strip() for part in SENTENCE_PATTERN.findall(text) if part.strip()]


def _article_text_by_source(db: Session | None, refs: list) -> dict[int, str]:
    """按 source_id 汇总条文级正文。

    语料导入的法源常常只在 legal_articles 里有正文（legal_sources.content 只是摘要），
    句级比对用条文正文才有意义，所以这里优先取条文、取不到再退回法源正文。
    """
    if db is None:
        return {}
    source_ids = {
        ref.get("source_id")
        for ref in (refs or [])
        if isinstance(ref, dict) and ref.get("source_id")
    }
    if not source_ids:
        return {}
    rows = db.query(LegalArticle).filter(LegalArticle.source_id.in_(source_ids)).all()
    texts: dict[int, str] = {}
    for article in rows:
        texts[article.source_id] = texts.get(article.source_id, "") + f"{article.article_number} {article.content}\n"
    return texts


def _dedupe_references(refs: list[dict]) -> list[dict]:
    """同一部法源 + 同一条号只保留第一次出现（模型偶尔会把同一条引用列两遍）。"""
    seen: set[tuple] = set()
    unique: list[dict] = []
    for ref in refs:
        key = (
            ref.get("source_id"),
            _normalize_source_title(ref.get("title") or ""),
            str(ref.get("citation") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def _reference_source_text(ref: dict, source_by_id: dict, article_text_by_source: dict | None = None) -> str:
    """引用参与比对的文本：优先取被引用法源的条文正文，其次法源正文，取不到才退回引用自身的标题与条号。"""
    source = source_by_id.get(ref.get("source_id")) if ref.get("source_id") else None
    if source is not None:
        article_text = (article_text_by_source or {}).get(source.id)
        body = article_text or source.full_text or source.content or ""
        return f"{source.title or ''} {source.citation or ''} {body}"
    return f"{ref.get('title') or ''} {ref.get('citation') or ''}"


def _normalize_law_name(value) -> str:
    """法源名的可比较形式：去掉书名号/括号/空白与「中华人民共和国」前缀、演示后缀。"""
    text = str(value or "")
    for token in ("《", "》", "〈", "〉", "（", "）", "(", ")", "【", "】", "[", "]", "　", " "):
        text = text.replace(token, "")
    text = text.replace("中华人民共和国", "")
    for suffix in ("演示依据", "演示模板", "演示"):
        text = text.replace(suffix, "")
    return text.strip()


def _named_reference_indexes(text: str, refs: list) -> list[int]:
    """本句里「被点名」的引用下标（句子里直接写了该法源名）。

    模型写「根据《劳动合同法》第八十二条」时，条号属于被点名的那部法；而句级词面映射
    可能同时把这句话挂到另一部法上（共享「一年/之日起/书面」这类词），若不加区分，
    另一部法会被误判成「引用的第 82 条未找到」。点名比词面重合更强，优先采信。
    """
    named: list[int] = []
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            continue
        for candidate in (_normalize_law_name(ref.get("title")), _normalize_law_name(ref.get("citation"))):
            if len(candidate) >= 3 and candidate in text:
                named.append(index)
                break
    return named


def _sentence_citation_numbers(refs: list, attribution: dict) -> dict[int, list[str]]:
    """从「已判定归属某条引用的句子」里收集条号，作为该引用的条级核验范围。

    模型经常把「第四十七条」写在正文里，而 references[].citation 只写书名；句级映射既然
    已经知道哪句属于哪条引用，就把这些条号交给核验，让条级核验有据可依（不改写模型原文）。

    归属优先级：句子里点名的法源 > 词面映射里得分最高的那一条引用。两者都比「挂到所有
    匹配上的引用」更准：否则「第四十七条」会被同时塞给共享词面的另一部法，把对方误判成
    「已核对一致」（条号恰好存在但含义不同）或「未找到」。
    """
    numbers_by_index: dict[int, list[str]] = {}
    for item in (attribution or {}).get("sentences") or []:
        text = str(item.get("text") or "")
        numbers = parse_citation_numbers(text)
        if not numbers:
            continue
        matched: list[tuple[int, float]] = []
        for match in item.get("matches") or []:
            index = match.get("reference_index")
            if not isinstance(index, int) or not (0 <= index < len(refs)):
                continue
            if not isinstance(refs[index], dict):
                continue
            raw_score = match.get("score")
            score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
            matched.append((index, score))
        indexes = _named_reference_indexes(text, refs)
        if not indexes:
            best = max((score for _, score in matched), default=None)
            tied = [index for index, score in matched if best is not None and score >= best]
            # 并列且不是同一部法（例如同一部法在库里有两条法源）时，无法判断条号属于谁：
            # 与其把「第四十七条」同时算进两部法（可能凑出假的「已核对一致」），不如不给。
            if len({_normalize_law_name(refs[index].get("title")) or _normalize_law_name(refs[index].get("citation")) for index in tied}) > 1:
                tied = []
            indexes = tied
        for index in indexes:
            bucket = numbers_by_index.setdefault(index, [])
            for number in numbers:
                if number not in bucket:
                    bucket.append(number)
    return numbers_by_index


def attribute_sentences_to_references(
    advice: str,
    references: list,
    sources: list[LegalSource],
    *,
    min_score: float = ATTRIBUTION_MIN_SCORE,
    max_matches: int = ATTRIBUTION_MAX_MATCHES,
    article_text_by_source: dict | None = None,
) -> dict:
    """把建议正文逐句对应到引用（句级溯源）。

    口径（宁可空着，也不编造）：
    - 只用词面重合度（jieba 分词后的交集 / 本句词数），不调用模型、不做语义猜测；
    - 低于 min_score 的句子**不给依据**：与其把整篇引用塞给每一句，不如承认这句没匹配上；
    - 引用没有 source_id 或法源没有正文时，用它自己的标题/条号参与比对，匹配不上就自然落空；
    - 返回空 dict 表示本次连句子或引用都拿不到，前端回退到「整篇答案」粒度。
    """
    sentences = split_answer_sentences(advice)
    refs = [ref for ref in (references or []) if isinstance(ref, dict)]
    if not sentences or not refs:
        return {}
    source_by_id = {source.id: source for source in (sources or [])}
    ref_tokens = [
        _tokenize(_reference_source_text(ref, source_by_id, article_text_by_source))
        for ref in refs
    ]
    if not any(ref_tokens):
        return {}

    items: list[dict] = []
    matched_sentences = 0
    for index, sentence in enumerate(sentences):
        tokens = _tokenize(sentence)
        matches: list[dict] = []
        if tokens:
            for ref_index, tokens_ref in enumerate(ref_tokens):
                if not tokens_ref:
                    continue
                shared = tokens & tokens_ref
                if not shared:
                    continue
                score = round(len(shared) / len(tokens), 4)
                if score < min_score:
                    continue
                matches.append({
                    "reference_index": ref_index,
                    "source_id": refs[ref_index].get("source_id"),
                    "title": refs[ref_index].get("title") or "",
                    "score": score,
                    "shared_terms": sorted(shared)[:6],
                })
        matches.sort(key=lambda item: (-item["score"], item["reference_index"]))
        matches = matches[:max_matches]
        if matches:
            matched_sentences += 1
        items.append({"index": index, "text": sentence, "matches": matches})

    return {
        "granularity": "sentence",
        "attribution_version": 1,
        "method": "lexical_overlap",
        "min_score": min_score,
        "total_sentences": len(sentences),
        "matched_sentences": matched_sentences,
        "sentences": items,
    }


# 注入 prompt 的参考上下文长度上限：与合同审查的正文截断（review_contract 的 8000 字）同口径。
CONTEXT_MAX_CHARS = 8000


def _prepare_extra_context(extra_context: dict | None) -> dict | None:
    """把调用方读到的「当前材料」整理成可注入文本 + 可如实记录的元数据。

    先过 `_redact_for_llm`（与问题同一口径），再截断到 `CONTEXT_MAX_CHARS`。
    拿不到正文就返回 None——宁可不注入，也不写一句编造的「已参考」。
    """
    if not isinstance(extra_context, dict):
        return None
    raw_text = extra_context.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    safe_text, _ = _redact_for_llm(raw_text)
    safe_text = safe_text.strip()[:CONTEXT_MAX_CHARS]
    if not safe_text:
        return None
    return {
        "type": str(extra_context.get("type") or "context"),
        "type_label": str(extra_context.get("type_label") or "参考材料"),
        "id": extra_context.get("id"),
        "title": str(extra_context.get("title") or "未命名"),
        "chars": len(safe_text),
        "text": safe_text,
    }


def _record_trace_context(trace_out: dict | None, context_meta: dict | None) -> None:
    """把「本次实际参考了哪份材料、多少字」写进 trace 顶层 contexts（与 steps/metrics 同级）。

    只记元数据不记正文：trace 会随响应回到前端，正文本来就在用户自己的记录里。
    """
    if trace_out is None or not context_meta:
        return
    trace_out["contexts"] = [{
        "type": context_meta["type"],
        "id": context_meta["id"],
        "title": context_meta["title"],
        "chars": context_meta["chars"],
    }]


async def consultation_payload(
    question: str,
    sources: list[LegalSource],
    user_id: int | None = None,
    db: Session | None = None,
    trace_out: dict | None = None,
    attribution_out: dict | None = None,
    extra_context: dict | None = None,
    step_callback=None,
):
    """LLM-powered legal consultation with deterministic fallback.

    `trace_out`（可选，由调用方传入空 dict）会按阶段写入过程 trace：检索法源 / 生成建议 /
    抽取事实 / 核验引用，每步带状态、耗时与依据。不传则完全不记录，行为与从前一致。

    `attribution_out`（可选，同样传空 dict）会写入句级引用映射：每句建议对应哪几条引用、
    词面重合度多少。不传则完全不计算，行为与从前一致。

    `extra_context`（可选）是调用方读到的「用户当前正在看的材料」（合同审查正文 / 文书正文），
    形如 `{"type", "type_label", "id", "title", "text"}`：只拼进 prompt 的问题段作为参考上下文，
    不参与法源排序，也不会被当成法源引用；实际用了哪份、多少字会如实写进 trace 顶层 `contexts`。
    不传则 prompt 与从前逐字一致。

    `step_callback`（可选，async 可调用）在每一步完成时立刻被调用，参数与该步的 trace 字典一致
    （`{id, label, status, duration_ms, detail}`）。WS 流式时间线用它「边跑边推」；推送失败只记
    warning，不影响咨询本身（回调异常不会中断链路，也不会污染 trace）。
    """
    steps: list[dict] = []

    async def _record(step: dict) -> None:
        # trace_out 负责落库，step_callback 负责实时推送，两者独立：只要有一个就继续
        if trace_out is not None:
            steps.append(step)
            trace_out["trace_version"] = 1
            trace_out["source"] = "legal_consultation"
            trace_out["steps"] = steps
        if step_callback is not None:
            try:
                await step_callback(step)
            except Exception:  # noqa: BLE001 - 推送失败不能影响咨询本身
                logger.warning("consultation step callback failed", exc_info=True)

    # 拒答：涉及违法/危害行为的实施求助，在进入 LLM 与检索前直接拒绝（合规红线）
    if _should_refuse(question):
        await _record(_trace_step(
            "refuse", "合规拦截", status="refused",
            detail="问题涉及违法或危害行为的实施求助，未进入检索与生成",
        ))
        return classify_question(question or ""), [], [], [], REFUSAL_ADVICE, "high", "needs_lawyer_review"
    # FL.md 7: redact PII before LLM call
    safe_question, redaction = _redact_for_llm(question)
    # 面板带进来的「当前材料」：只用于生成段，不参与下面的法源排序（避免正文把排序带偏）
    context_meta = _prepare_extra_context(extra_context)
    # FL.md 8.2: Agentic RAG source ranking
    retrieve_started = time.perf_counter()
    ranked_sources = _rank_sources_by_relevance(safe_question, sources)
    has_valid_source = any(s.status == "active" for s in ranked_sources)
    retrieve_ms = _elapsed_ms(retrieve_started)
    source_list = "\n".join(
        f"- ID:{s.id} {s.title}（{s.citation}，{s.version}）"
        for s in ranked_sources[:5]
    ) or "暂无法源数据"
    if ranked_sources:
        retrieve_detail = f"带回 {len(ranked_sources)} 条候选法源"
        if not has_valid_source:
            retrieve_detail += "，均未生效"
    else:
        retrieve_detail = "本次未检索到候选法源"
    await _record(_trace_step(
        "retrieve", "检索法源",
        status="success" if ranked_sources else "degraded",
        duration_ms=retrieve_ms,
        detail=retrieve_detail,
    ))
    required_facts_json = _dump(REQUIRED_FACTS)
    effective_disclaimer = DISCLAIMER if has_valid_source else f"{NO_VALID_SOURCE} {DISCLAIMER}"

    # 参考上下文拼进「用户问题」这一段，并写明它不是法源：模板变量契约不变，
    # 不需要为新变量发模板版本（模板缺变量是静默的，加了反而可能悄悄不生效）。
    prompt_question = safe_question
    if context_meta:
        prompt_question = (
            f"{safe_question}\n\n"
            f"参考上下文（{context_meta['type_label']}《{context_meta['title']}》"
            f"#{context_meta['id']}，仅本次提问；它不是法源，不能作为法律依据引用）：\n"
            f"{context_meta['text']}"
        )

    prompt = prompt_service.render_by_name(
        "legal_consultation",
        user_id=user_id,
        required_facts_json=required_facts_json,
        source_list=source_list,
        disclaimer=effective_disclaimer,
        question=prompt_question,
    )
    generate_started = time.perf_counter()
    llm_result = await _llm_chat(prompt, user_id, action="legal_consultation")
    generate_ms = _elapsed_ms(generate_started)

    if llm_result:
        parsed = _parse_json_safe(llm_result)
        if parsed and "category" in parsed:
            category = parsed.get("category", "other")
            known = parsed.get("known_facts", [])
            missing = parsed.get("missing_facts", [])
            advice = parsed.get("advice", "")
            risk = parsed.get("risk_level", "medium")
            refs = parsed.get("references", [])
            if not isinstance(refs, list):
                refs = []
            refs = [r for r in refs if isinstance(r, dict)]
            # Ensure references include source_id if available
            for ref in refs:
                if "source_id" not in ref:
                    matched_source = match_reference_to_source(ref, ranked_sources) or match_reference_to_source(ref, sources)
                    if matched_source is not None:
                        ref["source_id"] = matched_source.id
                        ref.update({k: v for k, v in ref_dict(matched_source).items() if k != "source_id"})
            # Fill references from ranked sources if LLM returned empty
            if not refs:
                refs = [ref_dict(s) for s in ranked_sources[:3]]
            # 去重：模型偶尔把同一部法源列两遍，重复引用会让句级映射出现无意义的一对多。
            refs = _dedupe_references(refs)
            # 句级映射先算：模型常把条号写在正文里、references[].citation 只写书名，
            # 这里把「被判定属于某条引用的句子」里出现的条号并入该引用的核验范围。
            attribution = {}
            if attribution_out is not None:
                attribution = attribute_sentences_to_references(
                    advice, refs, sources, article_text_by_source=_article_text_by_source(db, refs),
                )
            verify_started = time.perf_counter()
            refs = enrich_references(db, refs, sentence_numbers=_sentence_citation_numbers(refs, attribution))
            verify_ms = _elapsed_ms(verify_started)
            await _record(_trace_step(
                "generate", "生成建议", status="success", duration_ms=generate_ms,
                detail="模型返回结构化结果",
            ))
            await _record(_trace_step(
                "facts", "抽取事实", status="success",
                detail=f"已确认 {len(known)} 项 · 待补充 {len(missing)} 项",
            ))
            verified, unverified, unmarked = _reference_verification_counts(refs)
            if not refs:
                verify_detail, verify_status = "无法源引用，无需核验", "skipped"
            else:
                verify_detail = f"{verified} 条已核验 · {unverified} 条未核验"
                if unmarked:
                    verify_detail += f" · {unmarked} 条无核验字段"
                verify_status = "success"
            await _record(_trace_step(
                "verify", "核验引用", status=verify_status, duration_ms=verify_ms, detail=verify_detail,
            ))
            _attach_trace_metrics(
                trace_out, candidate_sources=len(ranked_sources), refs=refs,
                counts=(verified, unverified, unmarked),
            )
            _record_trace_context(trace_out, context_meta)
            if attribution_out is not None:
                attribution_out.update(attribution)
            status = "needs_lawyer_review" if risk == "high" or missing else "pending_review"
            return category, known, missing, refs, advice, risk, status
        await _record(_trace_step(
            "generate", "生成建议", status="degraded", duration_ms=generate_ms,
            detail="模型返回无法解析为结构化结果，已用规则兜底",
        ))
    else:
        await _record(_trace_step(
            "generate", "生成建议", status="degraded", duration_ms=generate_ms,
            detail="模型调用未返回结果，已用规则兜底",
        ))

    # Fallback: deterministic logic
    verify_started = time.perf_counter()
    category, known, missing, refs, advice, risk, status = _consultation_deterministic(safe_question, ranked_sources)
    refs = enrich_references(db, refs)
    verify_ms = _elapsed_ms(verify_started)
    await _record(_trace_step(
        "facts", "抽取事实", status="degraded",
        detail=f"按问题文本抽取：已确认 {len(known)} 项 · 待补充 {len(missing)} 项",
    ))
    verified, unverified, unmarked = _reference_verification_counts(refs)
    if not refs:
        verify_detail, verify_status = "无法源引用，无需核验", "skipped"
    else:
        verify_detail = f"{verified} 条已核验 · {unverified} 条未核验"
        if unmarked:
            verify_detail += f" · {unmarked} 条无核验字段"
        verify_status = "success"
    await _record(_trace_step(
        "verify", "核验引用", status=verify_status, duration_ms=verify_ms, detail=verify_detail,
    ))
    _attach_trace_metrics(
        trace_out, candidate_sources=len(ranked_sources), refs=refs,
        counts=(verified, unverified, unmarked),
    )
    _record_trace_context(trace_out, context_meta)
    if attribution_out is not None:
        attribution_out.update(attribute_sentences_to_references(
            advice, refs, sources, article_text_by_source=_article_text_by_source(db, refs),
        ))
    return category, known, missing, refs, advice, risk, status


def _consultation_deterministic(question: str, sources: list[LegalSource]):
    category = classify_question(question)
    known = [part.strip() for part in re.split(r"[，。；;\n]", question) if part.strip()][:6]
    missing = [fact for fact in REQUIRED_FACTS[category] if fact not in question]
    high_risk_words = ("刑事", "人身损害", "工伤", "时效", "大额", "证据不足", "逾期")
    high_risk = any(word in question for word in high_risk_words)
    refs = [ref_dict(source) for source in sources[:3]]
    status = "needs_lawyer_review" if high_risk or missing or not refs else "pending_review"
    # 无有效法源时与 LLM 路径（effective_disclaimer）保持一致：明确提示无有效依据
    has_valid_source = any(s.status == "active" for s in sources)
    no_source_prefix = f"{NO_VALID_SOURCE} " if not has_valid_source else ""
    advice = (
        f"{no_source_prefix}基于当前描述，可先围绕事实时间线、请求目标和证据完整性整理材料。"
        "系统仅提供一般性信息，不对胜诉、责任成立或金额结果作确定判断。"
        f" {DISCLAIMER}"
    )
    return category, known, missing, refs, advice, ("high" if high_risk else "medium" if missing else "low"), status


async def review_contract(content: str, user_id: int | None = None):
    """LLM-powered contract review with deterministic fallback."""
    # FL.md 7: redact PII before LLM call
    safe_content, _ = _redact_for_llm(content)
    prompt = prompt_service.render_by_name(
        "legal_contract_review",
        user_id=user_id,
        disclaimer=DISCLAIMER,
        content=safe_content,
    )
    llm_result = await _llm_chat(prompt, user_id, action="legal_contract_review")

    if llm_result:
        parsed = _parse_json_safe(llm_result)
        risks = parsed.get("risks") if isinstance(parsed, dict) else None
        # 形状校验：risks 必须是字典列表；畸形输出走确定性兜底，避免下游 KeyError/500
        if isinstance(risks, list) and all(isinstance(item, dict) for item in risks):
            summary = parsed.get("summary", "")
            if not summary:
                high_count = sum(1 for item in risks if item.get("risk_level") == "high")
                summary = f"共识别 {len(risks)} 项审查提示，其中高风险 {high_count} 项；结果需由审核律师结合原文确认。"
            return risks, summary

    # Fallback: deterministic logic
    return _review_contract_deterministic(content)


def _review_contract_deterministic(content: str):
    paragraphs = [item.strip() for item in re.split(r"\n+", content) if item.strip()]
    risks = []
    for index, paragraph in enumerate(paragraphs, start=1):
        for clause_type, keywords, label, level, suggestion in CLAUSE_RULES:
            hit = next((word for word in keywords if word in paragraph), None)
            if hit:
                start = paragraph.find(hit)
                risks.append(
                    {
                        "clause_type": clause_type,
                        "label": label,
                        "risk_level": level,
                        "description": f"第{index}段涉及{label}，需结合上下文核验权利义务是否完整。",
                        "source_location": {"paragraph": index, "start": start, "end": start + len(hit), "snippet": paragraph[:180]},
                        "suggestion": suggestion,
                        "status": "open",
                    }
                )
    present = {item["clause_type"] for item in risks}
    for clause_type, _, label, _, suggestion in CLAUSE_RULES:
        if clause_type not in present:
            risks.append(
                {
                    "clause_type": clause_type,
                    "label": label,
                    "risk_level": "medium",
                    "description": f"未识别到明确的{label}，建议补充或确认是否适用。",
                    "source_location": {"paragraph": None, "start": None, "end": None, "snippet": ""},
                    "suggestion": suggestion,
                    "status": "needs_facts",
                }
            )
    high_count = sum(1 for item in risks if item["risk_level"] == "high")
    summary = (
        f"共识别 {len(risks)} 项审查提示，其中高风险 {high_count} 项；结果需由审核律师结合原文确认。"
        f" {DISCLAIMER}"
    )
    return risks, summary


async def draft_content(document_type: str, fields: dict, missing: list[str], user_id: int | None = None) -> str:
    """LLM-powered legal draft generation with deterministic fallback."""
    type_labels = {
        "labor_arbitration_application": "劳动人事争议仲裁申请书",
        "private_lending_complaint": "民间借贷纠纷起诉状",
        "consumer_complaint": "消费纠纷投诉书",
        "supplementary_agreement": "补充协议",
    }
    document_type_label = type_labels.get(document_type, document_type)
    # FL.md 7: redact PII in field values before LLM call
    safe_fields = {}
    for k, v in fields.items():
        safe_v, _ = _redact_for_llm(str(v)) if v else (v, None)
        safe_fields[k] = safe_v
    fields_text = "\n".join(f"- {k}：{v or '【待补充】'}" for k, v in safe_fields.items()) or "未填写"
    missing_text = "、".join(missing) if missing else "无"

    prompt = prompt_service.render_by_name(
        "legal_draft_generation",
        user_id=user_id,
        document_type_label=document_type_label,
        missing_text=missing_text,
        disclaimer=DISCLAIMER,
        fields_text=fields_text,
    )
    llm_result = await _llm_chat(prompt, user_id, action="legal_draft_generation")

    if llm_result and len(llm_result.strip()) > 50:
        # Append disclaimer if LLM didn't include it
        if DISCLAIMER not in llm_result:
            llm_result += f"\n\n{DISCLAIMER}"
        return llm_result

    # Fallback: deterministic template
    return _draft_content_deterministic(document_type, fields, missing)


def _draft_content_deterministic(document_type: str, fields: dict, missing: list[str]) -> str:
    title = {
        "labor_arbitration_application": "劳动人事争议仲裁申请书（AI辅助草稿）",
        "private_lending_complaint": "民间借贷纠纷起诉状（AI辅助草稿）",
        "consumer_complaint": "消费纠纷投诉书（AI辅助草稿）",
        "supplementary_agreement": "补充协议（AI辅助草稿）",
    }[document_type]
    lines = [title, "", "一、当事人及基本信息"]
    for key, value in fields.items():
        lines.append(f"{key}：{value if value else '【待补充】'}")
    if missing:
        lines.extend(["", "待确认事实：", *[f"- {item}" for item in missing]])
    lines.extend(["", DISCLAIMER])
    return "\n".join(lines)


def target_query(db: Session, target_type: str, target_id: int):
    model = {"consultation": LegalConsultation, "contract_review": ContractReview, "draft": LegalDraft}.get(target_type)
    return db.query(model).filter(model.id == target_id).first() if model else None


def ref_dict(source: LegalSource) -> dict:
    """法源引用结构化字段：供 AI 输出引用核验（版本 + 效力状态 + 生效日期）。"""
    return {
        "source_id": source.id,
        "title": source.title,
        "citation": source.citation,
        "version": source.version,
        "status": source.status,
        "effective_date": str(source.effective_date) if source.effective_date else None,
        "jurisdiction": source.jurisdiction,
    }


async def consultation_followup(
    prev_question: str,
    prev_advice: str,
    followup_question: str,
    sources: list[LegalSource],
    user_id: int | None = None,
    db: Session | None = None,
):
    """Multi-turn follow-up for legal consultation."""
    # FL.md 7: redact PII before LLM call
    safe_followup, _ = _redact_for_llm(followup_question)
    safe_prev_q, _ = _redact_for_llm(prev_question)
    safe_prev_a, _ = _redact_for_llm(prev_advice)
    ranked_sources = _rank_sources_by_relevance(safe_followup, sources)
    source_list = "\n".join(
        f"- ID:{s.id} {s.title}（{s.citation}，{s.version}）"
        for s in ranked_sources[:5]
    ) or "暂无法源数据"

    prompt = prompt_service.render_by_name(
        "legal_followup",
        user_id=user_id,
        source_list=source_list,
        disclaimer=DISCLAIMER,
        prev_question=safe_prev_q[:2000],
        prev_advice=safe_prev_a[:4000],
        followup_question=safe_followup[:2000],
    )
    llm_result = await _llm_chat(prompt, user_id, action="legal_followup")

    if llm_result:
        parsed = _parse_json_safe(llm_result)
        if parsed and "advice" in parsed:
            category = parsed.get("category", "other")
            known = parsed.get("known_facts", [])
            missing = parsed.get("missing_facts", [])
            advice = parsed.get("advice", "")
            risk = parsed.get("risk_level", "medium")
            refs = parsed.get("references", [])
            if not isinstance(refs, list):
                refs = []
            refs = [r for r in refs if isinstance(r, dict)]
            for ref in refs:
                if "source_id" not in ref:
                    matched_source = match_reference_to_source(ref, ranked_sources) or match_reference_to_source(ref, sources)
                    if matched_source is not None:
                        ref["source_id"] = matched_source.id
                        ref.update({k: v for k, v in ref_dict(matched_source).items() if k != "source_id"})
            if not refs:
                refs = [ref_dict(s) for s in ranked_sources[:3]]
            refs = _dedupe_references(refs)
            refs = enrich_references(db, refs)
            status = "needs_lawyer_review" if risk == "high" or missing else "pending_review"
            return category, known, missing, refs, advice, risk, status

    # Fallback: append followup to previous advice
    advice = f"此前建议：{prev_advice}\n\n追问：{followup_question}\n\n基于现有信息，建议补充相关事实后由律师进一步判断。{DISCLAIMER}"
    return "other", [], [], [], advice, "medium", "pending_review"


# ── Contract Comparison ───────────────────────────────────────────

COMPARE_FIELDS = [
    ("sign_date", "签订日期"),
    ("total_amount", "合同总金额"),
    ("payment_terms", "付款条件"),
    ("delivery_date", "交付/验收日期"),
    ("responsible_party", "责任方"),
    ("breach_clause", "违约责任"),
    ("compensation_clause", "赔偿条款"),
    ("confidentiality_period", "保密期限"),
    ("termination_condition", "解除条件"),
    ("dispute_resolution", "争议解决方式"),
]


async def compare_contracts(
    content_a: str,
    content_b: str,
    title_a: str = "合同A",
    title_b: str = "合同B",
    user_id: int | None = None,
) -> dict:
    """LLM-powered contract comparison with deterministic fallback."""
    # FL.md 7: redact PII before LLM call
    safe_a, _ = _redact_for_llm(content_a)
    safe_b, _ = _redact_for_llm(content_b)
    prompt = prompt_service.render_by_name(
        "legal_contract_compare",
        user_id=user_id,
        title_a=title_a,
        title_b=title_b,
        disclaimer=DISCLAIMER,
        content_a=safe_a[:8000],
        content_b=safe_b[:8000],
    )
    llm_result = await _llm_chat(prompt, user_id, action="legal_contract_compare")

    if llm_result:
        parsed = _parse_json_safe(llm_result)
        if parsed and "fields" in parsed:
            fields = parsed["fields"]
            summary = parsed.get("summary", "")
            conflict_count = sum(1 for f in fields if f.get("conflict"))
            if not summary:
                summary = f"共对比 {len(fields)} 项关键字段，其中 {conflict_count} 项存在差异；请由审核律师确认最终口径。"
            return {"fields": fields, "summary": summary, "conflict_count": conflict_count}

    # Fallback: deterministic keyword-based comparison
    return _compare_contracts_deterministic(safe_a, safe_b, title_a, title_b)


def _compare_contracts_deterministic(content_a: str, content_b: str, title_a: str, title_b: str) -> dict:
    """Keyword-based fallback contract comparison."""
    field_keywords = {
        "sign_date": ("签订日期", "签署日期", "签订时间", "年.*月.*日"),
        "total_amount": ("总金额", "合同金额", "金额为", "万元"),
        "payment_terms": ("付款", "支付", "分期", "首付", "尾款"),
        "delivery_date": ("交付", "验收", "上线", "完成日期"),
        "responsible_party": ("甲方", "乙方", "责任方", "负责"),
        "breach_clause": ("违约", "违约金", "逾期"),
        "compensation_clause": ("赔偿", "补偿", "损害赔偿"),
        "confidentiality_period": ("保密", "保密期限", "保密义务"),
        "termination_condition": ("解除", "终止", "提前终止"),
        "dispute_resolution": ("争议", "仲裁", "诉讼", "管辖"),
    }

    fields = []
    for field_key, label in COMPARE_FIELDS:
        keywords = field_keywords.get(field_key, (field_key,))
        snippet_a = _find_snippet(content_a, keywords)
        snippet_b = _find_snippet(content_b, keywords)
        conflict = bool(snippet_a) != bool(snippet_b) or (snippet_a and snippet_b and snippet_a != snippet_b)
        severity = "high" if field_key in {"total_amount", "sign_date", "responsible_party"} and conflict else "medium" if conflict else "low"
        fields.append({
            "field": field_key,
            "label": label,
            "value_a": snippet_a or "未提及",
            "value_b": snippet_b or "未提及",
            "conflict": conflict,
            "severity": severity,
            "note": f"{'两份文件对该字段约定不一致' if conflict else '两份文件对该字段表述一致或均未提及'}",
        })

    conflict_count = sum(1 for f in fields if f["conflict"])
    summary = f"共对比 {len(fields)} 项关键字段，其中 {conflict_count} 项存在差异；请由审核律师确认最终口径。{DISCLAIMER}"
    return {"fields": fields, "summary": summary, "conflict_count": conflict_count}


def _find_snippet(content: str, keywords: tuple[str, ...]) -> str:
    """Find the first sentence containing any keyword."""
    for kw in keywords:
        idx = content.find(kw)
        if idx >= 0:
            start = max(0, content.rfind("\n", 0, idx) + 1)
            end = content.find("\n", idx)
            return content[start:end if end > 0 else len(content)].strip()[:200]
    return ""

