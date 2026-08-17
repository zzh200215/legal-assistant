"""分层采样（阶段 4 评估集治理）：按案件类型/文书类型/难度分层抽样。

- ``strata_of(case)``：从评测用例推导分层键（category / should_refuse / 难度代理）。
- ``stratified_sample(cases, n, strata_keys, seed)``：固定种子、按层成比例采样，
  返回 (sampled, stats)。同 seed + 同输入 → 同输出（可复现）。
- 采样方法文档见 docs/EVAL_METRICS.md §分层采样。
"""

from __future__ import annotations

import random
from collections import Counter, OrderedDict


def strata_of(case: dict) -> tuple[str, str, str]:
    """分层键：案件/文书类型（category）、是否拒答（should_refuse）、难度代理。

    难度代理：有 expected_answer_keywords 且 reference_answer 较短视为低难度；
    否则默认 medium；JSON 需重结构（should_refuse）视为 high。
    """
    category = str(case.get("category") or "other")
    refusal = "refuse" if bool(case.get("should_refuse")) else "normal"
    answer_keywords = [str(k) for k in (case.get("expected_answer_keywords") or []) if str(k).strip()]
    if refusal == "refuse" or len(answer_keywords) >= 3:
        difficulty = "high"
    elif len(answer_keywords) >= 1:
        difficulty = "medium"
    else:
        difficulty = "low"
    return (category, refusal, difficulty)


def stratified_sample(
    cases: list[dict],
    n: int,
    *,
    seed: int = 42,
    strata_keys: tuple[str, ...] = ("category", "refusal", "difficulty"),
) -> tuple[list[dict], dict]:
    """按层成比例采样（有放回配额分配，无放回抽取），返回 (样本, 统计)。"""
    if n <= 0 or not cases:
        return [], {"strata": {}, "sampled": 0, "total": len(cases)}
    rng = random.Random(seed)
    buckets: OrderedDict = OrderedDict()
    for case in cases:
        key = case_strata_key(case, strata_keys)
        buckets.setdefault(key, []).append(case)
    total = len(cases)
    stats: dict = {}
    sampled: list[dict] = []
    for key, pool in buckets.items():
        quota = round(len(pool) / total * n)
        chosen = pool if len(pool) <= quota else rng.sample(pool, quota)
        sampled.extend(chosen)
        stats[key] = {"pool": len(pool), "sampled": len(chosen)}
    # 配额取整可能少于 n：从剩余池按配额降序补齐
    remaining = n - len(sampled)
    if remaining > 0:
        leftovers = [c for c in cases if c not in sampled]
        for c in rng.sample(leftovers, min(remaining, len(leftovers))):
            sampled.append(c)
    return sampled, {"strata": stats, "sampled": len(sampled), "total": total}


def case_strata_key(case: dict, strata_keys: tuple[str, ...]) -> tuple[str, ...]:
    """按分层键列表组合键。strata_keys 支持 category/refusal/difficulty。"""
    parts = []
    for key in strata_keys:
        if key == "category":
            parts.append(str(case.get("category") or "other"))
        elif key == "refusal":
            parts.append("refuse" if bool(case.get("should_refuse")) else "normal")
        elif key == "difficulty":
            parts.append(strata_of(case)[2])
        else:
            parts.append(str(case.get(key) or ""))
    return tuple(parts)


def strata_counts(cases: list[dict]) -> Counter:
    """分层计数（诊断用）。"""
    return Counter(strata_of(c) for c in cases)
