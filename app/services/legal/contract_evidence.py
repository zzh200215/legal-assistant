"""合同审查「证据链」：风险 → 合同原文的定位与可核验性统计（纯函数，无 IO）。

口径（与前端 `useContractEvidence`、评测脚本 `run_contract_review_review` 一致）：
- **有定位** = `source_location.paragraph` 非空；
- **snippet 可核验** = 去掉空白后是合同原文的子串（截前 80 字，等价于 `_norm` 后 `in`）；
- 没有 snippet 的风险**不进 snippet 分母**（`snippet_verified=None`）；
- 分档：全部有 = strong；≥50% = partial；>0 = weak；0/空 = none。

为什么在服务端算：前端实时重算会与规则层漂移。这里在**读接口序列化时**现算，老记录也能
拿到同一口径的结果，不写库、不做迁移。
"""

from __future__ import annotations

from typing import Any

SNIPPET_MATCH_LIMIT = 80
LEVELS = ("strong", "partial", "weak", "none")
# 已被律师判定不适用的风险（忽略/驳回）不再计入缺口，否则误报的高风险会让审核永远过不了
GAP_EXCLUDED_STATUSES = {"dismissed"}


def _normalize(text: Any) -> str:
    return "".join(str(text or "").split())


def _snippet_verified(snippet: str, normalized_content: str) -> bool | None:
    if not snippet:
        return None
    return _normalize(snippet)[:SNIPPET_MATCH_LIMIT] in normalized_content


def _level(anchored: int, total: int) -> str:
    if total <= 0 or anchored <= 0:
        return "none"
    if anchored >= total:
        return "strong"
    return "partial" if anchored / total >= 0.5 else "weak"


def annotate_contract_evidence(content: str, risks: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """给每条风险补 `evidence`，并返回聚合统计；输入不合法时返回空列表 + 空统计。"""
    normalized_content = _normalize(content)
    items = [risk for risk in risks if isinstance(risk, dict)] if isinstance(risks, list) else []

    annotated: list[dict[str, Any]] = []
    anchored_count = 0
    snippet_count = 0
    verified_count = 0
    unanchored_by_level: dict[str, int] = {}
    dismissed_count = 0
    for risk in items:
        location = risk.get("source_location") if isinstance(risk.get("source_location"), dict) else {}
        paragraph = location.get("paragraph")
        snippet = str(location.get("snippet") or "").strip()
        anchored = paragraph is not None and paragraph != ""
        verified = _snippet_verified(snippet, normalized_content)
        if anchored:
            anchored_count += 1
        elif str(risk.get("status") or "") in GAP_EXCLUDED_STATUSES:
            dismissed_count += 1
        else:
            level = str(risk.get("risk_level") or "unknown")
            unanchored_by_level[level] = unanchored_by_level.get(level, 0) + 1
        if verified is not None:
            snippet_count += 1
            verified_count += 1 if verified else 0
        enriched = dict(risk)
        enriched["evidence"] = {
            "anchored": anchored,
            "paragraph": paragraph,
            "snippet_verified": verified,
        }
        annotated.append(enriched)

    total = len(annotated)
    active = total - dismissed_count  # 忽略掉的风险不参与覆盖率与缺口统计
    gaps_total = sum(unanchored_by_level.values())
    summary = {
        "total": total,
        "active": active,
        "anchored": anchored_count,
        "unanchored": gaps_total,
        "snippets": snippet_count,
        "verified": verified_count,
        "located_rate": round(anchored_count / active, 4) if active else 0.0,
        "snippet_rate": round(verified_count / snippet_count, 4) if snippet_count else None,
        "level": _level(anchored_count, active),
        # 缺口按等级拆开：高风险缺口是「必须补定位」，中低风险允许人工确认后通过
        "unanchored_high": unanchored_by_level.get("high", 0),
        "unanchored_by_level": unanchored_by_level,
        "dismissed": dismissed_count,
    }
    return annotated, summary
