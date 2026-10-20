"""合同审查链路的三层评审：规则层 + 跨模型裁判层 + 人工抽检表。

背景：`docs/review/step13-three-layer-review.md` / `step16-consultation-review.md` 把三层评审
落在 RAG QA 与法律咨询上；合同审查（`POST /api/legal/contract-reviews`）产出
`summary + risks_json`，有条款定位、风险等级和修改建议，却一直没跑过同口径评审。

本脚本把同一套三层口径搬到真实合同审查记录上：

- 规则层（零模型调用，跑全量去重后的审查记录）：
  风险条数 / 等级分布 / 条款类型覆盖 / 条款定位与 snippet 可核验性 / 建议完整度 /
  引用条数与法源有效状态 / 审查策略快照是否存在。
- 裁判层（跨模型，默认全部唯一记录）：
  ① 有据性——每条风险判断能否在合同原文找到依据（glm-5.2 与 qwen-plus 各判一次）；
  ② 质量——合同审查专用评分卡（风险覆盖 / 依据准确 / 建议可执行），同样两模型各判一次。
- 人工层：产出与 `scripts/score_human_review.py` 兼容的抽检表（每条两个条目：
  有据性 / 质量，两侧裁判判罚写进 meta），评分仍用同一个脚本。

用法（本机 python 子进程写不了 eval/outputs，故先写仓库根再 Move-Item）：

    python -B scripts/run_contract_review_review.py --dry-run
    python -B scripts/run_contract_review_review.py \
        --output .tmp_contract_review_review.json \
        --sheet-output .tmp_contract_review_spotcheck_sheet.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.legal import ContractReview  # noqa: E402
from app.services.legal.legal_service import CLAUSE_RULES  # noqa: E402
from run_consultation_review import _clip, _mean, _side_label  # noqa: E402
from run_cross_judge import JudgeClient, _PARSER  # noqa: E402

STANDARD_CLAUSES = [item[0] for item in CLAUSE_RULES]
STANDARD_LABELS = {item[0]: item[2] for item in CLAUSE_RULES}
RISK_LEVELS = ("high", "medium", "low", "needs_facts")
JUDGE_CONTRACT_CHARS = 6000
JUDGE_SUMMARY_CHARS = 800
UNSUPPORTED_MAX_ITEMS = 3
SHEET_CONTRACT_CHARS = 1000
SHEET_RISK_ITEMS = 5

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _loads(text: Any, fallback: Any) -> Any:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except (TypeError, ValueError):
        return fallback
    return value if value is not None else fallback


def _as_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _norm(text: Any) -> str:
    return "".join(str(text or "").split())


def _row_risks(row: ContractReview) -> list[dict[str, Any]]:
    return _as_dicts(_loads(row.risks_json, []))


def _row_refs(row: ContractReview) -> list[dict[str, Any]]:
    return _as_dicts(_loads(row.references_json, []))


# ---------------------------------------------------------------- 规则层（零模型调用）


def rule_metrics(row: ContractReview, content: str) -> dict[str, Any]:
    risks = _row_risks(row)
    refs = _row_refs(row)
    norm_content = _norm(content)

    anchored = 0
    snippets: list[str] = []
    snippet_verified = 0
    for risk in risks:
        location = risk.get("source_location") if isinstance(risk.get("source_location"), dict) else {}
        if location.get("paragraph") is not None:
            anchored += 1
        snippet = str(location.get("snippet") or "").strip()
        if snippet:
            snippets.append(snippet)
            if _norm(snippet)[:80] and _norm(snippet)[:80] in norm_content:
                snippet_verified += 1

    levels = Counter(str(risk.get("risk_level") or "unknown") for risk in risks)
    clause_types = sorted({str(risk.get("clause_type")) for risk in risks if risk.get("clause_type")})
    refs_with_source = sum(1 for ref in refs if ref.get("source_id"))
    refs_current = sum(
        1
        for ref in refs
        if isinstance(ref.get("verification"), dict) and ref["verification"].get("current_effective") is True
    )
    refs_superseded = sum(
        1
        for ref in refs
        if isinstance(ref.get("verification"), dict) and ref["verification"].get("superseded") is True
    )
    refs_unverified = sum(1 for ref in refs if not isinstance(ref.get("verification"), dict))

    return {
        "review_id": row.id,
        "user_id": row.user_id,
        "title": row.title or "",
        "status": row.status,
        "content_chars": len(content),
        "summary_chars": len(row.summary or ""),
        "risks": len(risks),
        "risk_levels": {level: levels.get(level, 0) for level in RISK_LEVELS if levels.get(level)}
        | {level: count for level, count in levels.items() if level not in RISK_LEVELS},
        "clause_types": clause_types,
        "clause_coverage": round(len(clause_types) / len(STANDARD_CLAUSES), 4),
        "suggestions": sum(1 for risk in risks if str(risk.get("suggestion") or "").strip()),
        "anchored": anchored,
        "anchor_rate": round(anchored / len(risks), 4) if risks else None,
        "snippets": len(snippets),
        "snippet_verified": snippet_verified,
        "snippet_verified_rate": round(snippet_verified / len(snippets), 4) if snippets else None,
        "refs": len(refs),
        "refs_with_source": refs_with_source,
        "refs_current": refs_current,
        "refs_superseded": refs_superseded,
        "refs_unverified": refs_unverified,
        "has_policy_snapshot": bool((row.review_policy_snapshot_json or "").strip() not in {"", "{}"}),
        "created_at": str(row.created_at or ""),
    }


def aggregate_rules(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if not total:
        return {"reviews": 0}
    risks_total = sum(row["risks"] for row in rows)
    anchored = sum(row["anchored"] for row in rows)
    snippets = sum(row["snippets"] for row in rows)
    snippet_verified = sum(row["snippet_verified"] for row in rows)
    suggestions = sum(row["suggestions"] for row in rows)
    refs_total = sum(row["refs"] for row in rows)
    by_level: Counter[str] = Counter()
    by_clause: Counter[str] = Counter()
    for row in rows:
        by_level.update(row["risk_levels"])
        by_clause.update(row["clause_types"])
    return {
        "reviews": total,
        "risks_total": risks_total,
        "risks_per_review": round(risks_total / total, 2),
        "risk_levels": dict(by_level),
        "high_risk_reviews": sum(1 for row in rows if row["risk_levels"].get("high")),
        "clause_coverage_avg": _mean([row["clause_coverage"] for row in rows]),
        "clause_types_seen": sorted(by_clause),
        "clause_types_never_seen": [item for item in STANDARD_CLAUSES if item not in by_clause],
        "suggestion_rate": round(suggestions / risks_total, 4) if risks_total else None,
        "anchor_rate": round(anchored / risks_total, 4) if risks_total else None,
        "reviews_without_anchor": sum(1 for row in rows if not row["anchored"]),
        "snippet_verified_rate": round(snippet_verified / snippets, 4) if snippets else None,
        "snippets_total": snippets,
        "references_total": refs_total,
        "refs_with_source": sum(row["refs_with_source"] for row in rows),
        "refs_current": sum(row["refs_current"] for row in rows),
        "refs_superseded": sum(row["refs_superseded"] for row in rows),
        "refs_unverified": sum(row["refs_unverified"] for row in rows),
        "reviews_with_policy_snapshot": sum(1 for row in rows if row["has_policy_snapshot"]),
        "content_chars_avg": round(sum(row["content_chars"] for row in rows) / total, 1),
        "summary_chars_avg": round(sum(row["summary_chars"] for row in rows) / total, 1),
    }


def dedupe_reviews(rows: list[ContractReview]) -> tuple[list[ContractReview], dict[str, Any]]:
    """同一段合同正文 + 同一份风险清单只保留最早一条（演示库里 4/5/6 是同一份）。"""
    seen: set[tuple[str, str]] = set()
    kept: list[ContractReview] = []
    dropped: list[int] = []
    for row in rows:
        key = (_norm(row.content), _norm(row.risks_json))
        if key in seen:
            dropped.append(row.id)
            continue
        seen.add(key)
        kept.append(row)
    return kept, {
        "reviews_total": len(rows),
        "reviews_unique": len(kept),
        "dropped_duplicate_ids": dropped,
        "dedupe_key": "content + risks_json（去掉空白后）",
    }


# ---------------------------------------------------------------- 裁判层


def _risk_lines(risks: list[dict[str, Any]]) -> str:
    lines = []
    for index, risk in enumerate(risks[:12], start=1):
        location = risk.get("source_location") if isinstance(risk.get("source_location"), dict) else {}
        label = risk.get("label") or STANDARD_LABELS.get(str(risk.get("clause_type")), risk.get("clause_type") or "未分类")
        snippet = _clip(location.get("snippet"), 160) or "无条款定位"
        lines.append(
            f"{index}. [{risk.get('risk_level') or 'unknown'}] {label}：{_clip(risk.get('description'), 260)}"
            f"（原文定位：{snippet}）"
        )
    return "\n".join(lines) or "（无风险条目）"


def _grounding_prompt(title: str, content: str, summary: str, risks: list[dict[str, Any]]) -> str:
    return (
        "你是合同审查结论的「有据性」审查员。只依据合同原文，不补充外部知识、不评价建议好坏。\n"
        "逐条判断审查结论中的事实性主张（风险等级、条款类型、风险描述、原文定位）能否在合同原文找到依据。\n"
        "判断规则：\n"
        "- 把合同里没有的事实说成有、或把有的说成没有 → 无依据；\n"
        "- 「合同未约定 X」属于对缺失的陈述，若原文确实没有 X，算有依据；\n"
        "- 修改建议是建议，不按有据/无据判；只看它引用的事实。\n"
        '输出 JSON：{"grounded": true, "unsupported": ["缺依据的断言"], "reason": "不超过60字"}\n\n'
        f"合同标题：{_clip(title, 120)}\n"
        f"合同原文：\n{_clip(content, JUDGE_CONTRACT_CHARS)}\n\n"
        f"审查摘要：\n{_clip(summary, JUDGE_SUMMARY_CHARS)}\n\n"
        f"风险清单：\n{_risk_lines(risks)}"
    )


def _quality_prompt(title: str, content: str, summary: str, risks: list[dict[str, Any]]) -> str:
    return (
        "你是合同审查结果的质量裁判。基于合同原文评价系统输出，不补充外部知识。\n"
        "评分维度（0-1）：\n"
        "- coverage：是否覆盖合同的主要风险类型（付款 / 交付 / 违约 / 赔偿 / 保密 / 知识产权 / 解除 / 争议解决等），有无重大遗漏；\n"
        "- grounding：风险描述与条款定位是否与原文一致，有无臆造；\n"
        "- actionability：修改建议是否具体、可落地（而不是「建议完善」这类空话）。\n"
        "verdict：pass（可接受）/ review（需人工复核）/ fail（不可接受，如与原文矛盾或漏掉核心风险）。\n"
        "只输出 JSON："
        '{"coverage":0.0,"grounding":0.0,"actionability":0.0,'
        '"verdict":"pass|review|fail","reason":"不超过80字"}\n\n'
        f"合同标题：{_clip(title, 120)}\n"
        f"合同原文：\n{_clip(content, JUDGE_CONTRACT_CHARS)}\n\n"
        f"审查摘要：\n{_clip(summary, JUDGE_SUMMARY_CHARS)}\n\n"
        f"风险清单：\n{_risk_lines(risks)}"
    )


async def judge_grounding(
    client: httpx.AsyncClient, judge: JudgeClient, case: dict[str, Any]
) -> dict[str, Any]:
    raw, error = await judge.complete(
        client, _grounding_prompt(case["title"], case["content"], case["summary"], case["risks"])
    )
    unavailable = {"available": False, "grounded": None, "unsupported": [], "reason": "judge_unavailable", "error": error}
    if raw is None:
        return unavailable
    payload = _PARSER.parse_json_object(raw)
    if not isinstance(payload, dict) or "grounded" not in payload:
        return {**unavailable, "error": "invalid_grounding_json", "raw_excerpt": _clip(raw, 400)}
    unsupported = payload.get("unsupported")
    items = [_clip(item, 120) for item in unsupported[:UNSUPPORTED_MAX_ITEMS]] if isinstance(unsupported, list) else []
    return {
        "available": True,
        "grounded": bool(payload.get("grounded")),
        "unsupported": [item for item in items if item],
        "reason": _clip(payload.get("reason"), 80),
        "error": None,
    }


def _clamp01(value: Any) -> float | None:
    try:
        return round(max(0.0, min(1.0, float(value))), 4)
    except (TypeError, ValueError):
        return None


def normalize_quality(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    verdict = str(data.get("verdict") or "review").strip().lower()
    if verdict not in {"pass", "review", "fail"}:
        verdict = "review"
    return {
        "coverage": _clamp01(data.get("coverage")),
        "grounding": _clamp01(data.get("grounding")),
        "actionability": _clamp01(data.get("actionability")),
        "verdict": verdict,
        "reason": _clip(data.get("reason"), 80),
    }


async def judge_quality(
    client: httpx.AsyncClient, judge: JudgeClient, case: dict[str, Any]
) -> dict[str, Any]:
    raw, error = await judge.complete(
        client, _quality_prompt(case["title"], case["content"], case["summary"], case["risks"])
    )
    unavailable = {
        "available": False,
        "coverage": None,
        "grounding": None,
        "actionability": None,
        "verdict": "review",
        "reason": "judge_unavailable",
        "error": error,
    }
    if raw is None:
        return unavailable
    payload = _PARSER.parse_json_object(raw)
    if not isinstance(payload, dict):
        return {**unavailable, "error": "invalid_quality_json", "raw_excerpt": _clip(raw, 400)}
    return {"available": True, "error": None, **normalize_quality(payload)}


def summarize_side(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grounding = [row for row in rows if row["grounding"].get("available")]
    quality = [row for row in rows if row["quality"].get("available")]
    return {
        "judged": len(rows),
        "grounding_available": len(grounding),
        "grounding_grounded": sum(1 for row in grounding if row["grounding"].get("grounded")),
        "grounding_rate": _mean([1.0 if row["grounding"].get("grounded") else 0.0 for row in grounding]),
        "unsupported_total": sum(len(row["grounding"].get("unsupported") or []) for row in grounding),
        "quality_available": len(quality),
        "quality_pass": sum(1 for row in quality if row["quality"].get("verdict") == "pass"),
        "quality_pass_rate": _mean([1.0 if row["quality"].get("verdict") == "pass" else 0.0 for row in quality]),
        "quality_dims": {
            dim: _mean([row["quality"][dim] for row in quality if row["quality"].get(dim) is not None])
            for dim in ("coverage", "grounding", "actionability")
        },
    }


def divergence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        glm_g, other_g = row["grounding"]["glm"], row["grounding"]["judge"]
        glm_q, other_q = row["quality"]["glm"], row["quality"]["judge"]
        grounded_diff = (
            glm_g.get("available") and other_g.get("available") and bool(glm_g.get("grounded")) != bool(other_g.get("grounded"))
        )
        quality_diff = (
            glm_q.get("available")
            and other_q.get("available")
            and (glm_q.get("verdict") == "pass") != (other_q.get("verdict") == "pass")
        )
        if grounded_diff or quality_diff:
            out.append(
                {
                    "review_id": row["review_id"],
                    "grounded_diff": bool(grounded_diff),
                    "quality_diff": bool(quality_diff),
                    "glm_grounded": glm_g.get("grounded"),
                    "judge_grounded": other_g.get("grounded"),
                    "glm_verdict": glm_q.get("verdict"),
                    "judge_verdict": other_q.get("verdict"),
                }
            )
    return out


# ---------------------------------------------------------------- 人工抽检表


def build_sheet(rows: list[dict[str, Any]], judge_label: str, meta_extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    lines = [
        "# 人工抽检：合同审查链路",
        "",
        f"> 由 `scripts/run_contract_review_review.py` 生成（{time.strftime('%Y-%m-%d %H:%M')}）；"
        "口径：**有据性**条目「正确」= 审查结论的事实性主张都能在合同原文找到依据；"
        "**质量**条目「正确」= 作为合同审查结果可接受（pass）。",
        "> 每条只填 `- 人工结论：` 三选一（`正确` / `错误` / `无法判断`），`备注` 自由；"
        "`无法判断` 与留空都不进一致率分母。评分：`python -B scripts/score_human_review.py --sheet <本表> --threshold 0.8`。",
        "",
        f"> 裁判：glm-5.2（与产品同族）与 {judge_label}（跨模型）。",
        "",
    ]
    ordinal = 0
    for row in rows:
        rid = row["review_id"]
        glm_g, other_g = row["grounding"]["glm"], row["grounding"]["judge"]
        glm_q, other_q = row["quality"]["glm"], row["quality"]["judge"]
        risk_brief = "\n".join(
            f"  - [{risk.get('risk_level') or 'unknown'}] {risk.get('label') or risk.get('clause_type') or '未分类'}："
            f"{_clip(risk.get('description'), 180)}"
            for risk in row["risks"][:SHEET_RISK_ITEMS]
        )
        ordinal += 1
        lines += [
            f"### {ordinal}. contract-{rid}-grounding｜有据性·合同审查",
            f"<!-- meta：id=contract-{rid}-grounding kind=faithfulness glm={_side_label(glm_g.get('available'), glm_g.get('grounded'))} {judge_label}={_side_label(other_g.get('available'), other_g.get('grounded'))} -->",
            "",
            f"- 类型：有据性｜合同：{_clip(row['title'], 120)}（风险 {len(row['risks'])} 条）",
            f"- 合同原文（节选）：{_clip(row['content'], SHEET_CONTRACT_CHARS)}",
            f"- 审查摘要：{_clip(row['summary'], 500)}",
            f"- 风险清单（前 {min(len(row['risks']), SHEET_RISK_ITEMS)} 条）：\n{risk_brief or '  （无）'}",
            "- 人工结论：",
            "- 备注：",
            "",
            "<details><summary>两位裁判意见（填完再看）</summary>",
            "",
            f"- **glm-5.2**：{_side_label(glm_g.get('available'), glm_g.get('grounded'))}｜"
            f"理由：{glm_g.get('reason') or glm_g.get('error') or '—'}",
            f"- **{judge_label}**：{_side_label(other_g.get('available'), other_g.get('grounded'))}｜"
            f"理由：{other_g.get('reason') or other_g.get('error') or '—'}",
            "",
            "</details>",
            "",
        ]
        ordinal += 1
        lines += [
            f"### {ordinal}. contract-{rid}-quality｜质量·合同审查",
            f"<!-- meta：id=contract-{rid}-quality kind=quality glm={_side_label(glm_q.get('available'), glm_q.get('verdict'))} {judge_label}={_side_label(other_q.get('available'), other_q.get('verdict'))} -->",
            "",
            f"- 类型：质量｜合同：{_clip(row['title'], 120)}（风险 {len(row['risks'])} 条）",
            f"- 合同原文（节选）：{_clip(row['content'], SHEET_CONTRACT_CHARS)}",
            f"- 审查摘要：{_clip(row['summary'], 500)}",
            f"- 风险清单（前 {min(len(row['risks']), SHEET_RISK_ITEMS)} 条）：\n{risk_brief or '  （无）'}",
            "- 人工结论：",
            "- 备注：",
            "",
            "<details><summary>两位裁判意见（填完再看）</summary>",
            "",
            f"- **glm-5.2**：{_side_label(glm_q.get('available'), glm_q.get('verdict'))}｜"
            f"coverage {glm_q.get('coverage')}｜grounding {glm_q.get('grounding')}｜actionability {glm_q.get('actionability')}｜"
            f"理由：{glm_q.get('reason') or glm_q.get('error') or '—'}",
            f"- **{judge_label}**：{_side_label(other_q.get('available'), other_q.get('verdict'))}｜"
            f"coverage {other_q.get('coverage')}｜grounding {other_q.get('grounding')}｜actionability {other_q.get('actionability')}｜"
            f"理由：{other_q.get('reason') or other_q.get('error') or '—'}",
            "",
            "</details>",
            "",
        ]
    stats = {"entries": ordinal, "reviews": len(rows), **meta_extra}
    return "\n".join(lines) + "\n", stats


# ---------------------------------------------------------------- 主流程


async def collect(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    db = SessionLocal()
    try:
        rows = db.query(ContractReview).order_by(ContractReview.id).all()
        unique, coverage = dedupe_reviews(rows)
        cases = []
        for row in unique:
            content = row.content or ""
            cases.append(
                {
                    "review_id": row.id,
                    "user_id": row.user_id,
                    "title": row.title or "",
                    "status": row.status,
                    "summary": row.summary or "",
                    "content": content,
                    "risks": _row_risks(row),
                    "references": _row_refs(row),
                }
            )
    finally:
        db.close()

    rules = [rule_metrics(unique[index], case["content"]) for index, case in enumerate(cases)]
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "legal_contract_reviews（真实合同审查记录，DB 直读）",
        "coverage": coverage,
        "rule_layer": aggregate_rules(rules),
        "cases": [],
    }
    if args.dry_run:
        report["judge_layer"] = {"skipped": "dry_run"}
        report["cases"] = [
            {"review_id": case["review_id"], "title": case["title"], "rule": rules[index], "risks": case["risks"]}
            for index, case in enumerate(cases)
        ]
        return report

    glm_client = JudgeClient(
        model=settings.LLM_MODEL,
        base_url=settings.LLM_API_BASE_URL,
        api_key=settings.LLM_API_KEY,
        min_interval=args.min_interval,
        backoff=args.backoff,
        timeout=args.timeout,
        max_tokens=4096,
    )
    judge_client = JudgeClient(
        model=args.judge_model,
        base_url=args.judge_base_url or settings.EMBEDDING_API_BASE_URL,
        api_key=args.judge_api_key or settings.EMBEDDING_API_KEY,
        min_interval=args.min_interval,
        backoff=args.backoff,
        timeout=args.timeout,
        max_tokens=2048,
    )
    if not (args.judge_api_key or settings.EMBEDDING_API_KEY):
        raise SystemExit("没有跨模型裁判凭据：请给 --judge-api-key，或在 .env 里配 EMBEDDING_API_KEY")

    judged: list[dict[str, Any]] = []
    started = time.monotonic()
    async with httpx.AsyncClient() as client:
        for index, case in enumerate(cases, start=1):
            glm_g = await judge_grounding(client, glm_client, case)
            other_g = await judge_grounding(client, judge_client, case)
            glm_q = await judge_quality(client, glm_client, case)
            other_q = await judge_quality(client, judge_client, case)
            judged.append(
                {
                    **{key: case[key] for key in ("review_id", "user_id", "title", "status", "summary", "content")},
                    "risks": case["risks"],
                    "references_total": len(case["references"]),
                    "rule": rules[index - 1],
                    "grounding": {"glm": glm_g, "judge": other_g},
                    "quality": {"glm": glm_q, "judge": other_q},
                }
            )
            print(
                f"[judge {index}/{len(cases)}] contract-{case['review_id']} "
                f"grounded glm={glm_g.get('grounded')} {judge_client.model}={other_g.get('grounded')} | "
                f"quality glm={glm_q.get('verdict')} {judge_client.model}={other_q.get('verdict')}",
                flush=True,
            )
    elapsed = round(time.monotonic() - started, 1)

    report["models"] = {
        "glm": {"model": glm_client.model, "endpoint": glm_client.url, "prompt": "合同审查：有据性 + 质量评分卡"},
        "judge": {"model": judge_client.model, "endpoint": judge_client.url},
    }
    report["judge_layer"] = {
        "glm": summarize_side([{"grounding": row["grounding"]["glm"], "quality": row["quality"]["glm"]} for row in judged]),
        "judge": summarize_side([{"grounding": row["grounding"]["judge"], "quality": row["quality"]["judge"]} for row in judged]),
        "elapsed_seconds": elapsed,
        "client_stats": {"glm": glm_client.stats(), "judge": judge_client.stats()},
    }
    report["divergence"] = divergence_rows(judged)
    report["cases"] = [
        {
            **{key: row[key] for key in ("review_id", "user_id", "title", "status", "summary", "content")},
            "risks": row["risks"],
            "references_total": row["references_total"],
            "rule": row["rule"],
            "grounding": row["grounding"],
            "quality": row["quality"],
        }
        for row in judged
    ]
    return report


def print_summary(report: dict[str, Any]) -> None:
    coverage, rules = report["coverage"], report["rule_layer"]
    deduped = ""
    if coverage["dropped_duplicate_ids"]:
        deduped = f"（丢弃重复 {len(coverage['dropped_duplicate_ids'])} 条：{coverage['dropped_duplicate_ids']}）"
    print("\n=== 合同审查三层评审 ===")
    print(f"审查记录 {coverage['reviews_total']} 条，去重后 {coverage['reviews_unique']} 条{deduped}")
    print(
        f"规则层：风险 {rules['risks_total']} 条（{rules['risk_levels']}）；"
        f"条款定位 {rules['anchor_rate']}、snippet 可核验 {rules['snippet_verified_rate']}"
        f"（{rules['snippets_total']} 条有 snippet）；建议 {rules['suggestion_rate']}；"
        f"高风险审查 {rules['high_risk_reviews']}/{rules['reviews']} 条"
    )
    print(
        f"        引用 {rules['references_total']} 条（带 source_id {rules['refs_with_source']}、"
        f"当前有效 {rules['refs_current']}、已废止 {rules['refs_superseded']}、无核验字段 {rules['refs_unverified']}）；"
        f"条款类型覆盖均值 {rules['clause_coverage_avg']}，未出现 {rules['clause_types_never_seen']}"
    )
    if "skipped" in report.get("judge_layer", {}):
        print(f"裁判层：跳过（{report['judge_layer']['skipped']}）")
        return
    for label, side in report["judge_layer"].items():
        if label in {"elapsed_seconds", "client_stats"}:
            continue
        print(
            f"裁判层 {label}：有据 {side['grounding_grounded']}/{side['grounding_available']}"
            f"（{side['grounding_rate']}，不支持断言 {side['unsupported_total']} 条）；"
            f"质量 pass {side['quality_pass']}/{side['quality_available']}（{side['quality_pass_rate']}）"
            f"｜三维 {side['quality_dims']}"
        )
    print(f"两模型分歧：{len(report.get('divergence') or [])} 条")
    print(f"耗时 {report['judge_layer']['elapsed_seconds']}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="合同审查链路的三层评审")
    parser.add_argument("--dry-run", action="store_true", help="只跑规则层，不调用模型")
    parser.add_argument("--judge-model", default="qwen-plus")
    parser.add_argument("--judge-base-url", default=None, help="默认取 .env 的 EMBEDDING_API_BASE_URL")
    parser.add_argument("--judge-api-key", default=None, help="默认取 .env 的 EMBEDDING_API_KEY")
    parser.add_argument("--min-interval", type=float, default=8.0, help="同一裁判两次请求的最小间隔秒数（默认 8）")
    parser.add_argument("--backoff", default="30,60,90,120,180")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", default=".tmp_contract_review_review.json")
    parser.add_argument("--sheet-output", default=".tmp_contract_review_spotcheck_sheet.md")
    parser.add_argument("--no-sheet", action="store_true")
    args = parser.parse_args()

    args.backoff = [float(item) for item in args.backoff.split(",") if item.strip()]
    report = asyncio.run(collect(args))
    (ROOT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.no_sheet and not args.dry_run:
        sheet, stats = build_sheet(
            report["cases"], args.judge_model, {"generated_at": report["generated_at"], "coverage": report["coverage"]}
        )
        (ROOT / args.sheet_output).write_text(sheet, encoding="utf-8")
        report["sheet"] = {"path": args.sheet_output, **stats}
        (ROOT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
