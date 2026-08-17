"""RAG 查询改写（Query Expansion）：法律术语同义/相关词扩展。

规则词典命中查询中的法律高频词，产出相关表述追加到检索变体，
扩大混合召回（BM25 + 向量）的覆盖面。确定性、零成本。
"""

# 法律高频术语 → 同义/相关表述
LEGAL_SYNONYMS: dict[str, list[str]] = {
    "工资": ["薪资", "劳动报酬", "薪酬"],
    "加班": ["加班费", "加班工资"],
    "解除": ["终止", "解约", "解除劳动合同"],
    "赔偿": ["补偿", "赔付", "损害赔偿", "违约金"],
    "合同": ["协议", "契约"],
    "违约": ["违约责任", "违约金"],
    "仲裁": ["劳动仲裁", "仲裁时效"],
    "诉讼": ["起诉", "诉讼时效"],
    "辞退": ["解雇", "开除", "解除劳动关系"],
    "休假": ["年休假", "带薪休假", "年假", "休息日"],
    "社保": ["社会保险", "五险一金"],
    "工伤": ["工伤保险", "工伤认定"],
    "离职": ["辞职", "经济补偿金"],
    "保密": ["竞业限制", "商业秘密"],
    "股权": ["股权激励", "股份"],
    "房屋": ["房产", "不动产", "租赁"],
    "债务": ["债权", "偿还"],
}


def expand_terms(query: str) -> list[str]:
    """返回查询中命中的法律术语对应的扩展词（去重、排除 query 已含词、保留顺序）。"""
    if not query:
        return []
    expanded: list[str] = []
    for term, related in LEGAL_SYNONYMS.items():
        if term not in query:
            continue
        for candidate in related:
            if candidate not in query and candidate not in expanded:
                expanded.append(candidate)
    return expanded
