"""检索打分与查询意图分析（纯函数，无实例状态）。

从 RAGService 抽出（P3 上帝类拆分第一步）。这些函数不依赖 self 状态，
独立成模块便于单元测试与复用；RAGService 保留同名私有方法委托调用。
"""

import re


def distance_score(distance: float | None) -> float:
    if distance is None:
        return 0.6
    return max(0.0, min(1.0, 1 - (distance / 2.0)))


def visual_region_alias_text(region: str | None) -> str:
    region_key = str(region or "").strip().lower()
    if not region_key:
        return ""
    mapping = {
        "top": ["top", "upper", "页面上部", "上部", "上方", "顶部"],
        "middle": ["middle", "center", "centre", "页面中部", "中部", "中间", "居中"],
        "bottom": ["bottom", "lower", "页面下部", "下部", "下方", "底部"],
    }
    return " ".join(mapping.get(region_key, [region_key]))


def visual_tag_match_bonus(query_variants: list[str], metadata: dict) -> float:
    query_text = " ".join(query_variants)
    visual_tags = set(str(metadata.get("visual_tags") or "").split())
    bonus = 0.0
    if visual_tags.intersection({"seal_present", "stamp_present"}) and re.search(r"(盖章|签章|公章|印章)", query_text):
        bonus += 0.12
    if visual_tags.intersection({"signature_present", "signed_page"}) and re.search(r"(签字|签署|签名)", query_text):
        bonus += 0.12
    if "attachment_like" in visual_tags and re.search(r"(附件|附录|附页)", query_text):
        bonus += 0.08
    if "table_visual" in visual_tags and re.search(r"(表格|表中|表里|表头|数据表)", query_text):
        bonus += 0.08
    return min(bonus, 0.2)


def query_prefers_table_like(query_variants: list[str]) -> bool:
    query_text = " ".join(query_variants)
    if re.search(r"\d{4}[-/年]\d{1,2}", query_text):
        return True
    return bool(
        re.search(
            r"(金额|付款|支付|费用|报价|价格|税率|比例|数量|统计|汇总|日期|时间|期限|截止|节点|发票|对账)",
            query_text,
        )
    )


def query_prefers_list_segment(query_variants: list[str]) -> bool:
    query_text = " ".join(query_variants)
    return bool(re.search(r"(步骤|流程|清单|列表|要求|材料|职责|安排|要点|范围|条件)", query_text))


def query_prefers_ocr_segment(query_variants: list[str]) -> bool:
    query_text = " ".join(query_variants)
    return bool(re.search(r"(扫描|扫描件|影印|图片|截图|拍照|照片|页码|第.?页|附图|原件)", query_text))


def query_mentions_page(query_variants: list[str]) -> bool:
    query_text = " ".join(query_variants)
    return bool(re.search(r"(第\s*\d+\s*页|页码|\d+\s*页)", query_text))


def query_prefers_visual_evidence(query_variants: list[str]) -> bool:
    query_text = " ".join(query_variants)
    return bool(re.search(r"(盖章|签章|公章|签字|签名|截图|照片|图片里|扫描件里|影印件)", query_text))


def query_mentions_table_capture(query_variants: list[str]) -> bool:
    query_text = " ".join(query_variants)
    return bool(re.search(r"(表格|表头|截图表格|表中|表里|列表图|数据表)", query_text))


def query_mentions_visual_region(query_variants: list[str], region: str | None) -> bool:
    region_aliases = visual_region_alias_text(region).split()
    if not region_aliases:
        return False
    query_text = " ".join(query_variants)
    return any(alias and alias in query_text for alias in region_aliases)


def extract_query_units(query: str) -> set[str]:
    terms = set(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", query))
    compact = re.sub(r"\s+", "", query)
    if re.search(r"[\u4e00-\u9fff]", compact):
        for index in range(len(compact) - 1):
            gram = compact[index : index + 2]
            if re.search(r"[\u4e00-\u9fff]{2}", gram):
                terms.add(gram)
    return {term for term in terms if len(term.strip()) >= 2}


def top_score_margin(chunks: list[dict]) -> float:
    """top 与次优候选 retrieval_score 的归一化差距；唯一/无分数候选给中性 0.5。"""
    scores = sorted(
        (float(chunk["retrieval_score"]) for chunk in chunks
         if chunk.get("retrieval_score") is not None),
        reverse=True,
    )
    if len(scores) < 2:
        return 0.5
    top, second = scores[0], scores[1]
    if top <= 0:
        return 0.0
    return max(0.0, min(1.0, (top - second) / top))


def route_consistency(chunks: list[dict]) -> float:
    """top hit 是否多路召回一致：dense + keyword 双路命中视为强一致。"""
    if not chunks:
        return 0.0
    routes = chunks[0].get("retrieval_routes") or []
    if not routes:
        return 0.5
    return 1.0 if len(routes) >= 2 else 0.3
