"""法律咨询（copilot 主链路）的三层评审：规则层 + 跨模型裁判层 + 人工抽检表。

背景：`docs/review/step13-three-layer-review.md` 的三层评审跑在 RAG QA 数据集上，
产品主链路（`POST /api/legal/consultations`）的答案质量一直没有同口径评审。
本脚本把同一套口径搬到真实咨询记录上：

- 规则层（零模型调用，跑全量去重后的咨询）：
  引用条数 / 条级核验三态 / 句级映射命中率 / trace 各步状态与耗时 / 是否带用户材料。
- 裁判层（跨模型，只跑抽样）：
  ① 忠实度——与产品内置判官同 prompt（`agentic_judge.check_faithfulness` 口径），
     glm-5.2 与 qwen-plus 各判一次；
  ② 质量——咨询专用评分卡（无标准答案，只能依据「问题 + 本次证据」判断），同样两模型各判一次。
- 人工层：产出与 `scripts/score_human_review.py` 兼容的抽检表（每条两个条目：
  忠实度 / 质量，两侧裁判判罚写进 meta），评分仍用同一个脚本。

用法（本机 python 子进程写不了 eval/outputs，故先写仓库根再 Move-Item）：

    python -B scripts/run_consultation_review.py --sample 8 \
        --output .tmp_consultation_review.json \
        --sheet-output .tmp_consultation_spotcheck_sheet.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from app.core.config import get_settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.legal import (  # noqa: E402
    ContractReview,
    LegalArticle,
    LegalConsultation,
    LegalDraft,
    LegalSource,
)
from eval.llm_judge import _normalize_judgement  # noqa: E402
from run_cross_judge import JudgeClient, _faithfulness_prompt, _PARSER  # noqa: E402

EVIDENCE_MAX_CHUNKS = 6
EVIDENCE_EXCERPT_CHARS = 300
MATERIAL_EXCERPT_CHARS = 1200
UNSUPPORTED_MAX_ITEMS = 3

_QUALITY_DIMS = ("groundedness", "answer_relevance", "completeness")
_REPEAT_PREFIX = re.compile(r"^第\s*\d+\s*次[：:]\s*")


def _clip(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _loads(text: Any, fallback: Any) -> Any:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except (TypeError, ValueError):
        return fallback
    return value if value is not None else fallback


# ---------------------------------------------------------------- 规则层（零模型调用）


def _verification_counts(refs: list[dict[str, Any]]) -> dict[str, int]:
    verified = unverified = unmarked = 0
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        verification = ref.get("verification")
        flag = verification.get("verified") if isinstance(verification, dict) else None
        if flag is True:
            verified += 1
        elif flag is False:
            unverified += 1
        else:
            unmarked += 1
    return {"verified": verified, "unverified": unverified, "unmarked": unmarked}


def rule_metrics(row: LegalConsultation) -> dict[str, Any]:
    refs = _loads(row.references_json, [])
    refs = [item for item in refs if isinstance(item, dict)] if isinstance(refs, list) else []
    attribution = _loads(row.attribution_json, {})
    trace = _loads(row.trace_json, {})
    steps = trace.get("steps") if isinstance(trace, dict) else None
    steps = steps if isinstance(steps, list) else []
    contexts = trace.get("contexts") if isinstance(trace, dict) else None
    contexts = contexts if isinstance(contexts, list) else []
    sentences = attribution.get("sentences") if isinstance(attribution, dict) else None
    sentences = sentences if isinstance(sentences, list) else []
    matched = sum(1 for item in sentences if isinstance(item, dict) and item.get("matches"))
    generate = next((step for step in steps if isinstance(step, dict) and step.get("id") == "generate"), {})
    return {
        "consultation_id": row.id,
        "user_id": row.user_id,
        "question": row.question or "",
        "status": row.status,
        "advice_chars": len(row.advice or ""),
        "references": len(refs),
        "reference_sources": len({ref.get("source_id") for ref in refs if ref.get("source_id")}),
        "verification": _verification_counts(refs),
        "attribution_granularity": attribution.get("granularity") if isinstance(attribution, dict) else None,
        "sentences_total": len(sentences),
        "sentences_matched": matched,
        "trace_steps": len(steps),
        "generate_status": generate.get("status"),
        "generate_ms": generate.get("duration_ms"),
        "material_contexts": [item.get("type") for item in contexts if isinstance(item, dict)],
    }


def aggregate_rules(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if not total:
        return {"consultations": 0}
    verified = sum(row["verification"]["verified"] for row in rows)
    unverified = sum(row["verification"]["unverified"] for row in rows)
    unmarked = sum(row["verification"]["unmarked"] for row in rows)
    refs_total = verified + unverified + unmarked
    sentences_total = sum(row["sentences_total"] for row in rows)
    sentences_matched = sum(row["sentences_matched"] for row in rows)
    graded = [row for row in rows if row["generate_status"]]
    degraded = [row for row in graded if row["generate_status"] != "success"]
    durations = [row["generate_ms"] for row in graded if isinstance(row["generate_ms"], (int, float))]
    with_material = [row for row in rows if row["material_contexts"]]
    return {
        "consultations": total,
        "with_advice": sum(1 for row in rows if row["advice_chars"] > 0),
        "references_total": refs_total,
        "verification": {
            "verified": verified,
            "unverified": unverified,
            "unmarked": unmarked,
            "verified_rate": round(verified / refs_total, 4) if refs_total else None,
        },
        "attribution": {
            "sentences_total": sentences_total,
            "sentences_matched": sentences_matched,
            "matched_rate": round(sentences_matched / sentences_total, 4) if sentences_total else None,
            "consultations_with_mapping": sum(1 for row in rows if row["sentences_total"]),
        },
        "trace": {
            "consultations_with_trace": sum(1 for row in rows if row["trace_steps"]),
            "generate_graded": len(graded),
            "generate_degraded": len(degraded),
            "generate_degraded_rate": round(len(degraded) / len(graded), 4) if graded else None,
            "generate_ms_avg": round(sum(durations) / len(durations), 1) if durations else None,
        },
        "material": {
            "consultations_with_material": len(with_material),
            "by_type": {
                kind: sum(1 for row in rows if kind in row["material_contexts"])
                for kind in sorted({kind for row in rows for kind in row["material_contexts"]})
            },
        },
    }


# ---------------------------------------------------------------- 抽样


def _question_family(question: str) -> str:
    text = _REPEAT_PREFIX.sub("", (question or "").strip())
    return text[:24] or "unknown"


def build_sample(rows: list[dict[str, Any]], sample: int, seed: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """确定性抽样：按「是否带用户材料」分层，层内按 id 等距取，再按问题族去重。

    同一份答复（question + advice 完全相同）只保留最早的一条，避免同一段文字被反复判分。
    """
    ordered = sorted(rows, key=lambda row: row["consultation_id"])
    strata: dict[str, list[dict[str, Any]]] = {}
    for row in ordered:
        key = "with_material" if row["material_contexts"] else "text_only"
        strata.setdefault(key, []).append(row)
    picked: list[dict[str, Any]] = []
    families: set[str] = set()
    index = 0
    keys = sorted(strata)
    while len(picked) < sample and any(index < len(strata[key]) for key in keys):
        for key in keys:
            bucket = strata[key]
            if index >= len(bucket) or len(picked) >= sample:
                continue
            row = bucket[index]
            family = _question_family(row["question"])
            if family in families:
                continue
            families.add(family)
            picked.append(row)
        index += 1
    proof = {
        "seed": seed,
        "requested": sample,
        "selected": len(picked),
        "dedupe_key": "question + advice",
        "strata_sizes": {key: len(value) for key, value in strata.items()},
        "selected_ids": [row["consultation_id"] for row in picked],
    }
    return picked, proof


# ---------------------------------------------------------------- 证据重建


def build_evidence(db, row: LegalConsultation) -> list[dict[str, str]]:
    """按产品内置判官的口径重建证据：引用条文（无 source_id 时退回引用自身）+ 用户材料。

    每个片段带 `kind`：`article`（法源库里查到了条文正文）/ `reference`（只有引用标题与条号，
    法源库未收录条文——判官与人工都只能看到条号，必须在表里标出来）/ `material`（用户材料）。
    """
    refs = _loads(row.references_json, [])
    refs = [item for item in refs if isinstance(item, dict)] if isinstance(refs, list) else []
    chunks: list[dict[str, str]] = []
    for ref in refs:
        if len(chunks) >= EVIDENCE_MAX_CHUNKS:
            break
        source_id = ref.get("source_id")
        articles: list[LegalArticle] = []
        if source_id:
            articles = (
                db.query(LegalArticle)
                .filter(LegalArticle.source_id == source_id)
                .order_by(LegalArticle.sequence)
                .limit(4)
                .all()
            )
        if articles:
            body = " ".join(f"{item.article_number} {item.content}" for item in articles if item.content)
            chunks.append({"kind": "article", "content": _clip(f"《{ref.get('title') or ''}》{body}", EVIDENCE_EXCERPT_CHARS)})
        else:
            fallback = f"{ref.get('title') or ''} {ref.get('citation') or ''}".strip()
            if fallback:
                chunks.append({"kind": "reference", "content": _clip(fallback, EVIDENCE_EXCERPT_CHARS)})
    trace = _loads(row.trace_json, {})
    contexts = trace.get("contexts") if isinstance(trace, dict) else None
    for context in contexts if isinstance(contexts, list) else []:
        if len(chunks) >= EVIDENCE_MAX_CHUNKS or not isinstance(context, dict):
            break
        kind, item_id = context.get("type"), context.get("id")
        body = None
        if kind == "contract_review":
            material = db.get(ContractReview, item_id)
            body = material.content if material else None
        elif kind == "draft":
            material = db.get(LegalDraft, item_id)
            body = material.content if material else None
        if body:
            chunks.append({"kind": "material", "content": _clip(f"{context.get('title') or kind}\n{body}", MATERIAL_EXCERPT_CHARS)})
    return chunks


# ---------------------------------------------------------------- 裁判层


def _quality_prompt(question: str, answer: str, chunks: list[dict[str, str]]) -> str:
    evidence = "\n".join(f"[片段 {index}] {_clip(chunk['content'], MATERIAL_EXCERPT_CHARS)}" for index, chunk in enumerate(chunks, start=1))
    return (
        "你是法律咨询答复的质量裁判。你没有标准答案，只能依据「用户问题 + 本次证据」判断"
        "这份答复作为法律信息答复是否可接受，不能补充外部知识。\n"
        "评分维度（0-1）：\n"
        "- groundedness：答复中的事实性断言（法条号、期限、金额、程序）能否在证据中找到依据；\n"
        "- answer_relevance：是否正面回答了用户问的问题；\n"
        "- completeness：是否覆盖问题的关键要点（适用条件、计算标准、时效、救济途径等）。\n"
        "verdict：pass（可接受）/ review（需人工复核）/ fail（不可接受，如与证据矛盾或答非所问）。\n"
        "只输出 JSON："
        '{"groundedness":0.0,"answer_relevance":0.0,"completeness":0.0,'
        '"verdict":"pass|review|fail","reason":"不超过80字"}\n\n'
        f"用户问题：{_clip(question, 1200)}\n"
        f"系统答复：{_clip(answer, 3000)}\n"
        f"本次证据：\n{evidence or '（无证据）'}"
    )


async def judge_faithfulness(client: httpx.AsyncClient, judge: JudgeClient, question: str, answer: str, chunks: list[dict[str, str]]) -> dict[str, Any]:
    raw, error = await judge.complete(client, _faithfulness_prompt(question, answer, chunks))
    if raw is None:
        return {"available": False, "faithful": None, "unsupported": [], "reason": "faithfulness_unavailable", "error": error}
    payload = _PARSER.parse_json_object(raw)
    if not isinstance(payload, dict) or "faithful" not in payload:
        return {
            "available": False,
            "faithful": None,
            "unsupported": [],
            "reason": "faithfulness_unavailable",
            "error": "invalid_faithfulness_json",
            "raw_excerpt": _clip(raw, 400),
        }
    unsupported = payload.get("unsupported")
    items = [_clip(item, 100) for item in unsupported[:UNSUPPORTED_MAX_ITEMS]] if isinstance(unsupported, list) else []
    return {
        "available": True,
        "faithful": bool(payload.get("faithful")),
        "unsupported": [item for item in items if item],
        "reason": _clip(payload.get("reason"), 80),
        "error": None,
    }


async def judge_quality(client: httpx.AsyncClient, judge: JudgeClient, question: str, answer: str, chunks: list[dict[str, str]]) -> dict[str, Any]:
    raw, error = await judge.complete(client, _quality_prompt(question, answer, chunks))
    unavailable = {
        "available": False,
        "groundedness": None,
        "answer_relevance": None,
        "completeness": None,
        "verdict": "review",
        "reason": "judge_unavailable",
        "error": error,
    }
    if raw is None:
        return unavailable
    payload = _PARSER.parse_json_object(raw)
    if not payload:
        return {**unavailable, "error": "invalid_judge_json", "raw_excerpt": _clip(raw, 400)}
    return {"available": True, "error": None, **_normalize_judgement(payload)}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def summarize_side(rows: list[dict[str, Any]]) -> dict[str, Any]:
    faithful = [row for row in rows if row["faithfulness"].get("available")]
    quality = [row for row in rows if row["quality"].get("available")]
    return {
        "judged": len(rows),
        "faithfulness_available": len(faithful),
        "faithfulness_faithful": sum(1 for row in faithful if row["faithfulness"].get("faithful")),
        "faithfulness_rate": _mean([1.0 if row["faithfulness"].get("faithful") else 0.0 for row in faithful]),
        "unsupported_total": sum(len(row["faithfulness"].get("unsupported") or []) for row in faithful),
        "quality_available": len(quality),
        "quality_pass": sum(1 for row in quality if row["quality"].get("verdict") == "pass"),
        "quality_pass_rate": _mean([1.0 if row["quality"].get("verdict") == "pass" else 0.0 for row in quality]),
        "quality_dims": {dim: _mean([float(row["quality"][dim]) for row in quality if row["quality"].get(dim) is not None]) for dim in _QUALITY_DIMS},
    }


def divergence_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        glm_f, other_f = row["faithfulness"]["glm"], row["faithfulness"]["judge"]
        glm_q, other_q = row["quality"]["glm"], row["quality"]["judge"]
        faith_diff = (
            glm_f.get("available") and other_f.get("available") and bool(glm_f.get("faithful")) != bool(other_f.get("faithful"))
        )
        quality_diff = (
            glm_q.get("available")
            and other_q.get("available")
            and (glm_q.get("verdict") == "pass") != (other_q.get("verdict") == "pass")
        )
        if faith_diff or quality_diff:
            out.append(
                {
                    "consultation_id": row["consultation_id"],
                    "faithfulness_diff": bool(faith_diff),
                    "quality_diff": bool(quality_diff),
                    "glm_faithful": glm_f.get("faithful"),
                    "judge_faithful": other_f.get("faithful"),
                    "glm_verdict": glm_q.get("verdict"),
                    "judge_verdict": other_q.get("verdict"),
                }
            )
    return out


# ---------------------------------------------------------------- 人工抽检表


def _side_label(available: bool, value: Any) -> str:
    if not available:
        return "不可用"
    if isinstance(value, bool):
        return "忠实" if value else "不忠实"
    return "pass" if value == "pass" else str(value or "review")


def _evidence_block(chunks: list[dict[str, str]]) -> str:
    if not chunks:
        return "（本次没有可用的证据片段）"
    labels = {"article": "法源条文", "reference": "仅条号（法源库未收录条文）", "material": "用户材料"}
    return " ／ ".join(
        f"`[片段 {index}·{labels.get(chunk.get('kind', ''), chunk.get('kind') or '证据')}] {_clip(chunk['content'], 400)}`"
        for index, chunk in enumerate(chunks, start=1)
    )


def build_sheet(rows: list[dict[str, Any]], judge_label: str, meta_extra: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    lines = [
        "# 人工抽检：法律咨询（copilot 主链路）",
        "",
        f"> 由 `scripts/run_consultation_review.py` 生成（{time.strftime('%Y-%m-%d %H:%M')}）；"
        "口径：**忠实度**条目「正确」= 答复的事实性断言都能在证据里找到依据；**质量**条目「正确」= 答复质量可接受（可判 pass）。",
        "> 每条只填 `- 人工结论：` 三选一（`正确` / `错误` / `无法判断`），`备注` 自由；"
        "`无法判断` 与留空都不进一致率分母。评分：`python -B scripts/score_human_review.py --sheet <本表> --threshold 0.8`。",
        "",
        f"> 裁判：glm-5.2（产品内置判官同 prompt）与 {judge_label}（跨模型）。",
        "",
    ]
    ordinal = 0
    for row in rows:
        cid = row["consultation_id"]
        glm_f, other_f = row["faithfulness"]["glm"], row["faithfulness"]["judge"]
        glm_q, other_q = row["quality"]["glm"], row["quality"]["judge"]
        ordinal += 1
        lines += [
            f"### {ordinal}. consult-{cid}-faith｜忠实度·法律咨询",
            f"<!-- meta：id=consult-{cid}-faith kind=faithfulness glm={_side_label(glm_f.get('available'), glm_f.get('faithful'))} {judge_label}={_side_label(other_f.get('available'), other_f.get('faithful'))} -->",
            "",
            f"- 类型：忠实度｜问题：{_clip(row['question'], 200)}",
            f"- 系统答复：{_clip(row['advice'], 1200)}",
            f"- 本次证据（法源条文 / 用户材料）：{_evidence_block(row['evidence'])}",
            "- 人工结论：",
            "- 备注：",
            "",
            "<details><summary>两位裁判意见（填完再看）</summary>",
            "",
            f"- **glm-5.2**：{_side_label(glm_f.get('available'), glm_f.get('faithful'))}｜理由：{glm_f.get('reason') or glm_f.get('error') or '—'}",
            f"- **{judge_label}**：{_side_label(other_f.get('available'), other_f.get('faithful'))}｜理由：{other_f.get('reason') or other_f.get('error') or '—'}",
            "",
            "</details>",
            "",
        ]
        ordinal += 1
        lines += [
            f"### {ordinal}. consult-{cid}-quality｜质量·法律咨询",
            f"<!-- meta：id=consult-{cid}-quality kind=quality glm={_side_label(glm_q.get('available'), glm_q.get('verdict'))} {judge_label}={_side_label(other_q.get('available'), other_q.get('verdict'))} -->",
            "",
            f"- 类型：质量｜问题：{_clip(row['question'], 200)}",
            f"- 系统答复：{_clip(row['advice'], 1200)}",
            f"- 本次证据（法源条文 / 用户材料）：{_evidence_block(row['evidence'])}",
            "- 人工结论：",
            "- 备注：",
            "",
            "<details><summary>两位裁判意见（填完再看）</summary>",
            "",
            f"- **glm-5.2**：{_side_label(glm_q.get('available'), glm_q.get('verdict'))}｜"
            f"groundedness {glm_q.get('groundedness')}｜relevance {glm_q.get('answer_relevance')}｜completeness {glm_q.get('completeness')}｜"
            f"理由：{glm_q.get('reason') or glm_q.get('error') or '—'}",
            f"- **{judge_label}**：{_side_label(other_q.get('available'), other_q.get('verdict'))}｜"
            f"groundedness {other_q.get('groundedness')}｜relevance {other_q.get('answer_relevance')}｜completeness {other_q.get('completeness')}｜"
            f"理由：{other_q.get('reason') or other_q.get('error') or '—'}",
            "",
            "</details>",
            "",
        ]
    stats = {"entries": ordinal, "consultations": len(rows), **meta_extra}
    return "\n".join(lines) + "\n", stats


# ---------------------------------------------------------------- 主流程


def load_consultations(db) -> list[LegalConsultation]:
    return (
        db.query(LegalConsultation)
        .filter(LegalConsultation.advice.isnot(None))
        .order_by(LegalConsultation.id)
        .all()
    )


def dedupe(rows: list[LegalConsultation]) -> tuple[list[LegalConsultation], int]:
    seen: set[tuple[str, str]] = set()
    kept: list[LegalConsultation] = []
    dropped = 0
    for row in rows:
        key = ((row.question or "").strip(), (row.advice or "").strip())
        if not key[1]:
            dropped += 1
            continue
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(row)
    return kept, dropped


async def collect(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    db = SessionLocal()
    try:
        raw_rows = load_consultations(db)
        unique_rows, dropped = dedupe(raw_rows)
        rules = [rule_metrics(row) for row in unique_rows]
        sample_rows, proof = build_sample(rules, args.sample, args.seed)
        by_id = {row.id: row for row in unique_rows}
        sampled = [by_id[item["consultation_id"]] for item in sample_rows]
        evidence = {row.id: build_evidence(db, row) for row in sampled}
    finally:
        db.close()

    glm_client = JudgeClient(
        model=settings.LLM_MODEL,
        base_url=settings.LLM_API_BASE_URL,
        api_key=settings.LLM_API_KEY,
        min_interval=args.min_interval,
        backoff=args.backoff,
        timeout=args.timeout,
    )
    judge_client = JudgeClient(
        model=args.judge_model,
        base_url=args.judge_base_url or settings.EMBEDDING_API_BASE_URL,
        api_key=args.judge_api_key or settings.EMBEDDING_API_KEY,
        min_interval=args.min_interval,
        backoff=args.backoff,
        timeout=args.timeout,
    )
    if not (args.judge_api_key or settings.EMBEDDING_API_KEY):
        raise SystemExit("没有跨模型裁判凭据：请给 --judge-api-key，或在 .env 里配 EMBEDDING_API_KEY")

    judged: list[dict[str, Any]] = []
    started = time.monotonic()
    async with httpx.AsyncClient() as client:
        for index, row in enumerate(sampled, start=1):
            chunks = evidence[row.id]
            question, answer = row.question or "", row.advice or ""
            glm_faith = await judge_faithfulness(client, glm_client, question, answer, chunks)
            other_faith = await judge_faithfulness(client, judge_client, question, answer, chunks)
            glm_quality = await judge_quality(client, glm_client, question, answer, chunks)
            other_quality = await judge_quality(client, judge_client, question, answer, chunks)
            judged.append(
                {
                    "consultation_id": row.id,
                    "question": question,
                    "advice": answer,
                    "evidence": chunks,
                    "rule": next(item for item in sample_rows if item["consultation_id"] == row.id),
                    "faithfulness": {"glm": glm_faith, "judge": other_faith},
                    "quality": {"glm": glm_quality, "judge": other_quality},
                }
            )
            print(
                f"[judge {index}/{len(sampled)}] consult-{row.id} "
                f"faith glm={glm_faith.get('faithful')} {judge_client.model}={other_faith.get('faithful')} | "
                f"quality glm={glm_quality.get('verdict')} {judge_client.model}={other_quality.get('verdict')}",
                flush=True,
            )
    elapsed = round(time.monotonic() - started, 1)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": "legal_consultations（真实咨询记录，DB 直读）",
        "models": {
            "glm": {"model": glm_client.model, "endpoint": glm_client.url, "action": "咨询判官（忠实度同 prompt / 质量评分卡）"},
            "judge": {"model": judge_client.model, "endpoint": judge_client.url},
        },
        "sampling": proof,
        "coverage": {
            "consultations_total": len(raw_rows),
            "consultations_after_dedupe": len(unique_rows),
            "dropped_duplicates_or_empty": dropped,
        },
        "rule_layer": aggregate_rules(rules),
        "judge_layer": {
            "glm": summarize_side([{"faithfulness": row["faithfulness"]["glm"], "quality": row["quality"]["glm"]} for row in judged]),
            "judge": summarize_side([{"faithfulness": row["faithfulness"]["judge"], "quality": row["quality"]["judge"]} for row in judged]),
            "elapsed_seconds": elapsed,
            "client_stats": {"glm": glm_client.stats(), "judge": judge_client.stats()},
        },
        "divergence": divergence_rows(judged),
        "cases": [
            {
                "consultation_id": row["consultation_id"],
                "question": row["question"],
                "advice": row["advice"],
                "evidence_chunks": len(row["evidence"]),
                "evidence_kinds": {kind: sum(1 for chunk in row["evidence"] if chunk.get("kind") == kind) for kind in ("article", "reference", "material")},
                "evidence": row["evidence"],
                "rule": row["rule"],
                "faithfulness": row["faithfulness"],
                "quality": row["quality"],
            }
            for row in judged
        ],
    }
    return report


def print_summary(report: dict[str, Any]) -> None:
    coverage = report["coverage"]
    rules = report["rule_layer"]
    print("\n=== 法律咨询三层评审（copilot 主链路）===")
    print(
        f"咨询总数 {coverage['consultations_total']}｜去重后 {coverage['consultations_after_dedupe']}"
        f"（丢弃重复/空答复 {coverage['dropped_duplicates_or_empty']}）｜抽样判定 {report['sampling']['selected']} 条"
    )
    print(
        f"规则层：引用 {rules['references_total']} 条，条级核验通过 {rules['verification']['verified']}"
        f"（{rules['verification']['verified_rate']}）、未通过 {rules['verification']['unverified']}、无核验字段 {rules['verification']['unmarked']}"
    )
    print(
        f"        句级映射 {rules['attribution']['sentences_matched']}/{rules['attribution']['sentences_total']}"
        f"（{rules['attribution']['matched_rate']}）；trace 覆盖 {rules['trace']['consultations_with_trace']} 条，"
        f"生成降级 {rules['trace']['generate_degraded']}/{rules['trace']['generate_graded']}；带材料 {rules['material']['consultations_with_material']} 条"
    )
    for label, side in report["judge_layer"].items():
        if label in {"elapsed_seconds", "client_stats"}:
            continue
        print(
            f"裁判层 {label}：忠实度 {side['faithfulness_faithful']}/{side['faithfulness_available']}"
            f"（{side['faithfulness_rate']}，不支持断言 {side['unsupported_total']} 条）；"
            f"质量 pass {side['quality_pass']}/{side['quality_available']}（{side['quality_pass_rate']}）"
            f"｜三维 {side['quality_dims']}"
        )
    print(f"两模型分歧：{len(report['divergence'])} 条")
    cases = report.get("cases") or []
    with_article = [case for case in cases if case.get("evidence_kinds", {}).get("article")]
    with_material = [case for case in cases if case.get("evidence_kinds", {}).get("material")]
    print(
        f"证据口径：抽样 {len(cases)} 条中 {len(with_article)} 条能看到法源条文正文"
        f"（其余只有条号），{len(with_material)} 条带用户材料"
    )
    print(f"耗时 {report['judge_layer']['elapsed_seconds']}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="法律咨询（copilot 主链路）的三层评审")
    parser.add_argument("--sample", type=int, default=8, help="抽样的去重咨询条数（默认 8；每条 4 次裁判调用）")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--judge-model", default="qwen-plus")
    parser.add_argument("--judge-base-url", default=None, help="默认取 .env 的 EMBEDDING_API_BASE_URL")
    parser.add_argument("--judge-api-key", default=None, help="默认取 .env 的 EMBEDDING_API_KEY")
    parser.add_argument("--min-interval", type=float, default=8.0, help="同一裁判两次请求的最小间隔秒数（默认 8）")
    parser.add_argument("--backoff", default="30,60,90,120,180")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", default=".tmp_consultation_review.json")
    parser.add_argument("--sheet-output", default=".tmp_consultation_spotcheck_sheet.md")
    parser.add_argument("--sheet-meta", default=None, help="抽样自证 JSON（默认 <sheet>.build.json）")
    parser.add_argument("--no-sheet", action="store_true")
    args = parser.parse_args()

    args.backoff = [float(item) for item in args.backoff.split(",") if item.strip()]
    report = asyncio.run(collect(args))
    (ROOT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.no_sheet:
        sheet, stats = build_sheet(report["cases"], args.judge_model, {"generated_at": report["generated_at"], "sampling": report["sampling"]})
        (ROOT / args.sheet_output).write_text(sheet, encoding="utf-8")
        meta_path = args.sheet_meta or f"{args.sheet_output}.build.json"
        (ROOT / meta_path).write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["sheet"] = {"path": args.sheet_output, **stats}
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
