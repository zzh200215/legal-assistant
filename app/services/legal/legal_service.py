"""Legal-workspace service with LLM-powered analysis.

Falls back to deterministic keyword logic when LLM is unavailable.
Generated text is always framed as AI-assisted and missing facts are
surfaced instead of being invented.
"""

import json
import logging
import re
from datetime import date

import jieba
from sqlalchemy.orm import Session

from app.models.legal import (
    ContractReview,
    LegalConsultation,
    LegalDraft,
    LegalSource,
)
from app.services.org.data_protection_service import data_protection_service
from app.services.legal.legal_reference_service import enrich_references
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
    },
    {
        "title": "《中华人民共和国劳动合同法》第40、46、47条（演示依据）",
        "source_type": "statute",
        "citation": "劳动合同法第40、46、47条",
        "jurisdiction": "中国大陆",
        "version": "现行版本（需复核）",
        "content": "无过失性辞退需提前30日通知或额外支付一个月工资（第40条）；用人单位依第四十条解除应支付经济补偿（第46条）；经济补偿按工作年限每满一年支付一个月工资（第47条）。正式使用前应核验最新法源。",
    },
    {
        "title": "《中华人民共和国劳动争议调解仲裁法》第27条（演示依据）",
        "source_type": "statute",
        "citation": "劳动争议调解仲裁法第27条",
        "jurisdiction": "中国大陆",
        "version": "现行版本（需复核）",
        "content": "劳动争议仲裁时效为一年，自当事人知道或应当知道其权利被侵害之日起计算。此条目用于演示检索链路，正式使用前应核验最新法源。",
    },
    {
        "title": "《最高人民法院关于审理民间借贷案件适用法律若干问题的规定》（演示依据）",
        "source_type": "statute",
        "citation": "民间借贷司法解释",
        "jurisdiction": "中国大陆",
        "version": "现行版本（需复核）",
        "content": "民间借贷利率上限、举证责任及合同效力的一般规则。此条目用于演示检索链路，正式使用前应核验最新法源。",
    },
    {
        "title": "《中华人民共和国消费者权益保护法》第55条（演示依据）",
        "source_type": "statute",
        "citation": "消费者权益保护法第55条",
        "jurisdiction": "中国大陆",
        "version": "现行版本（需复核）",
        "content": "经营者提供商品或服务有欺诈行为的，应增加赔偿其受到的损失，增加赔偿的金额为消费者购买商品价款的3倍。正式使用前应核验最新法源。",
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
        logger.warning("LLM call failed, falling back to deterministic logic: %s", exc)
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
        db.add(
            LegalSource(
                user_id=user_id,
                effective_date=date.today(),
                status="active",
                **item,
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


async def consultation_payload(question: str, sources: list[LegalSource], user_id: int | None = None, db: Session | None = None):
    """LLM-powered legal consultation with deterministic fallback."""
    # 拒答：涉及违法/危害行为的实施求助，在进入 LLM 与检索前直接拒绝（合规红线）
    if _should_refuse(question):
        return classify_question(question or ""), [], [], [], REFUSAL_ADVICE, "high", "needs_lawyer_review"
    # FL.md 7: redact PII before LLM call
    safe_question, redaction = _redact_for_llm(question)
    # FL.md 8.2: Agentic RAG source ranking
    ranked_sources = _rank_sources_by_relevance(safe_question, sources)
    has_valid_source = any(s.status == "active" for s in ranked_sources)
    source_list = "\n".join(
        f"- ID:{s.id} {s.title}（{s.citation}，{s.version}）"
        for s in ranked_sources[:5]
    ) or "暂无法源数据"
    required_facts_json = _dump(REQUIRED_FACTS)
    effective_disclaimer = DISCLAIMER if has_valid_source else f"{NO_VALID_SOURCE} {DISCLAIMER}"

    prompt = prompt_service.render_by_name(
        "legal_consultation",
        user_id=user_id,
        required_facts_json=required_facts_json,
        source_list=source_list,
        disclaimer=effective_disclaimer,
        question=safe_question,
    )
    llm_result = await _llm_chat(prompt, user_id, action="legal_consultation")

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
                    for s in ranked_sources:
                        if s.title == ref.get("title") or s.citation == ref.get("citation"):
                            ref["source_id"] = s.id
                            ref.update({k: v for k, v in ref_dict(s).items() if k != "source_id"})
                            break
            # Fill references from ranked sources if LLM returned empty
            if not refs:
                refs = [ref_dict(s) for s in ranked_sources[:3]]
            refs = enrich_references(db, refs)
            status = "needs_lawyer_review" if risk == "high" or missing else "pending_review"
            return category, known, missing, refs, advice, risk, status

    # Fallback: deterministic logic
    category, known, missing, refs, advice, risk, status = _consultation_deterministic(safe_question, ranked_sources)
    return category, known, missing, enrich_references(db, refs), advice, risk, status


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
                    for s in ranked_sources:
                        if s.title == ref.get("title") or s.citation == ref.get("citation"):
                            ref["source_id"] = s.id
                            ref.update({k: v for k, v in ref_dict(s).items() if k != "source_id"})
                            break
            if not refs:
                refs = [ref_dict(s) for s in ranked_sources[:3]]
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

