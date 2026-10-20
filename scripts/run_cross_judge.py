"""交叉模型裁判：换一个模型家族复判同一批答案，并生成给人抽检的分歧清单。

为什么需要它：`faithfulness_report.json` 与 `qa_eval_repaired.json` 的判分都由 `glm-5.2`
自己给出——裁判与被评答案同源，1.0 只能说明「按该裁判口径没发现问题」。诊断文档 §8 因此
要求换模型家族复判同一批答案。§8 写的是 `deepseek-v4.1-flash`，但仓库里没有任何 deepseek
凭据；实测 `.env` 的 `EMBEDDING_*`（dashscope 兼容模式）可直接调 chat 模型，故本轮用
`qwen-plus`（凭据零新增）；换模型只需 `--judge-model` / `--judge-base-url` / `--judge-api-key`。

只换「模型 + HTTP 出口」，两条 prompt 均原样复用现成口径：

- 忠实度：`app/services/rag/agentic_judge.py` 的 `check_faithfulness`——连截断常量
  （`_ANSWER_MAX_CHARS` / `_UNSUPPORTED_MAX_ITEMS`）与 `_evidence_block` 一起 import，
  prompt 正文逐字照抄；
- 三维质量：`eval/llm_judge.py` 的 `_build_prompt` / `_normalize_judgement`——直接 import
  函数本体，连归一化（0-1 截断、verdict 白名单）都走同一份代码。

不重跑检索与生成，只吃已有产物，成本 = 每题的裁判往返；因此它测的是「裁判口径差」，
不是「答案质量」。

用法：
  # ① 真跑一轮交叉裁判（真实调用；--min-interval 10 是按被测模型 7 次/分钟的限流定的）
  $env:PYTHONPATH="<根>"
  python -B scripts/run_cross_judge.py --output cross_judge_report.json `
      --sheet-output divergence_review_sheet.md --min-interval 10

  # ② 从已有报告生成分层抽样人工抽检表（确定性抽样；零模型调用，用于「人工 vs 裁判一致率」验收）
  python -B scripts/run_cross_judge.py --sheet-mode stratified --sheet-sample 24 `
      --from-report eval/outputs/cross_judge_report.json --sheet-output divergence_review_sheet.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import get_settings
from app.services.llm.llm_service import LLMService
from app.services.rag.agentic_judge import (
    _ANSWER_MAX_CHARS,
    _UNSUPPORTED_MAX_ITEMS,
    _clip,
    _evidence_block,
)
from eval.llm_judge import _build_prompt as build_quality_prompt
from eval.llm_judge import _normalize_judgement

_PARSER = LLMService()  # 只借它的 parse_json_object：与两个既有口径的解析方式逐字一致

SCORE_STEP = 0.1  # 「一档」= 0.1 分，质量维度的分歧阈值
_QUALITY_DIMS = ("groundedness", "answer_relevance", "completeness")
_SHEET_MAX_QUALITY = 30
_SHEET_ANSWER_CHARS = 500
_SHEET_REFERENCE_CHARS = 320
_SHEET_EVIDENCE_CHARS = 200
_SHEET_EVIDENCE_ITEMS = 2


def _faithfulness_prompt(question: str, answer: str, chunks: list[dict[str, Any]]) -> str:
    """`agentic_judge.check_faithfulness` 的 prompt 原文，只把调用方换成这里的 HTTP 出口。"""
    return (
        "你是企业知识库答案审查员。逐条检查回答中的事实性断言（数字、期限、金额、条件、"
        "责任主体）能否在片段中找到依据。片段之外的常识性表述不算不忠实。\n"
        '输出 JSON：{"faithful": true, "unsupported": ["缺依据的断言"], "reason": "不超过40字"}\n\n'
        f"问题：{_clip(question, 500)}\n"
        f"回答：{_clip(answer, _ANSWER_MAX_CHARS)}\n\n"
        f"{_evidence_block(chunks)}"
    )


def _evidence_chunks(case: dict[str, Any]) -> list[dict[str, Any]]:
    """与 `scripts/run_faithfulness_eval.py` 完全相同的证据构造：只吃写进答案的引用片段。"""
    return [
        {"content": citation.get("source_text") or "", "metadata": {"document_id": citation.get("document_id")}}
        for citation in case.get("citations") or []
        if citation.get("source_text")
    ]


class JudgeClient:
    """单个裁判模型 + 单条出口 + 全局节流；两轮复判共用同一条请求节奏。"""

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        min_interval: float,
        backoff: list[float],
        timeout: float = 120.0,
        max_tokens: int = 1024,
    ) -> None:
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._min_interval = min_interval
        self._backoff = backoff
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._last_start = 0.0
        self.attempts = 0
        self.ok_calls = 0
        self.retries = 0
        self.rate_limit_hits = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.failures: list[str] = []

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_start
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_start = time.monotonic()

    async def complete(self, client: httpx.AsyncClient, prompt: str) -> tuple[str | None, str | None]:
        """返回 ``(文本, 失败原因)``；失败不抛异常——跑不完也要留下真实的 N/总数 记录。"""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": self._max_tokens,
        }
        last_error = "unknown"
        for attempt in range(len(self._backoff) + 1):
            await self._throttle()
            self.attempts += 1
            try:
                response = await client.post(self.url, headers=self._headers, json=payload, timeout=self._timeout)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            else:
                if response.status_code == 200:
                    self.ok_calls += 1
                    return self._extract(response)
                last_error = f"{response.status_code} {response.text.strip()[:200]}"
                if response.status_code == 429:
                    self.rate_limit_hits += 1
                elif response.status_code < 500:
                    self.failures.append(f"HTTP {response.status_code}: {response.text.strip()[:200]}")
                    return None, f"http_{response.status_code}"
            if attempt == len(self._backoff):
                break
            self.retries += 1
            await asyncio.sleep(self._backoff[attempt])
        self.failures.append(last_error)
        return None, last_error

    def _extract(self, response: httpx.Response) -> tuple[str | None, str | None]:
        data = response.json()
        usage = data.get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        choices = data.get("choices") or []
        text = str((choices[0].get("message") or {}).get("content") or "") if choices else ""
        if not text.strip():
            self.failures.append("empty_content")
            return None, "empty_content"
        return text, None

    def stats(self) -> dict[str, Any]:
        return {
            "judge_model": self.model,
            "endpoint": self.url,
            "min_interval_seconds": self._min_interval,
            "backoff_seconds": self._backoff,
            "max_tokens": self._max_tokens,
            "http_attempts": self.attempts,
            "ok_calls": self.ok_calls,
            "retries": self.retries,
            "rate_limit_429_hits": self.rate_limit_hits,
            "failed_calls": self.attempts - self.ok_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "failure_samples": self.failures[:5],
        }


async def judge_faithfulness_http(
    client: httpx.AsyncClient, judge: JudgeClient, case: dict[str, Any]
) -> dict[str, Any]:
    chunks = _evidence_chunks(case)
    raw, error = await judge.complete(client, _faithfulness_prompt(case["question"], case.get("answer") or "", chunks))
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
    items = [_clip(item, 100) for item in unsupported[:_UNSUPPORTED_MAX_ITEMS]] if isinstance(unsupported, list) else []
    return {
        "available": True,
        "faithful": bool(payload.get("faithful")),
        "unsupported": [item for item in items if item],
        "reason": _clip(payload.get("reason"), 80),
        "error": None,
    }


async def judge_quality_http(client: httpx.AsyncClient, judge: JudgeClient, case: dict[str, Any]) -> dict[str, Any]:
    raw, error = await judge.complete(client, build_quality_prompt(case))
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


def _quality_means(rows: list[dict[str, Any]]) -> dict[str, Any]:
    available = [row for row in rows if row.get("available")]
    summary: dict[str, Any] = {
        "cases": len(rows),
        "judged": len(available),
        "unavailable": len(rows) - len(available),
    }
    for field in _QUALITY_DIMS:
        scores = [float(row[field]) for row in available if row.get(field) is not None]
        summary[field] = round(sum(scores) / len(scores), 4) if scores else None
    summary["pass_rate"] = (
        round(sum(row.get("verdict") == "pass" for row in available) / len(available), 4) if available else None
    )
    return summary


def _faithfulness_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [row for row in rows if row.get("available")]
    faithful = [row for row in judged if row.get("faithful") is True]
    return {
        "cases": len(rows),
        "judged": len(judged),
        "unavailable": len(rows) - len(judged),
        "faithful_cases": len(faithful),
        "faithfulness_rate": round(len(faithful) / len(judged), 4) if judged else None,
        "cases_with_unsupported_claims": sum(1 for row in judged if row.get("unsupported")),
        "unsupported_claims_total": sum(len(row.get("unsupported") or []) for row in judged),
    }


def _diff_faithfulness(glm: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    both = bool(glm.get("available")) and bool(other.get("available"))
    faithful_changed = both and glm.get("faithful") != other.get("faithful")
    unsupported_only = both and not faithful_changed and sorted(glm.get("unsupported") or []) != sorted(
        other.get("unsupported") or []
    )
    return {"faithful_changed": faithful_changed, "unsupported_only": unsupported_only, "divergent": faithful_changed or unsupported_only}


def _diff_quality(glm: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    deltas: dict[str, float | None] = {}
    for field in _QUALITY_DIMS:
        left, right = glm.get(field), other.get(field)
        deltas[field] = round(abs(float(left) - float(right)), 4) if left is not None and right is not None else None
    known = [value for value in deltas.values() if value is not None]
    max_delta = max(known) if known else None
    both = bool(glm.get("available")) and bool(other.get("available"))
    verdict_changed = both and glm.get("verdict") != other.get("verdict")
    return {
        "deltas": deltas,
        "max_delta": max_delta,
        "verdict_changed": verdict_changed,
        "divergent": both and (verdict_changed or (max_delta is not None and max_delta >= SCORE_STEP)),
    }


async def collect(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    result = json.loads((ROOT / args.result).read_text(encoding="utf-8"))
    glm_faithfulness = json.loads((ROOT / args.faithfulness).read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = result["cases"]
    by_name = {case["name"]: case for case in cases}

    glm_faith_rows = {row["name"]: row for row in glm_faithfulness["cases"]}
    faith_names = [name for name in glm_faith_rows if name in by_name]
    missing_names = [name for name in glm_faith_rows if name not in by_name]
    if args.limit:
        faith_names = faith_names[: args.limit]
    quality_cases = cases[: args.limit] if args.limit else list(cases)

    base_url = args.judge_base_url or settings.EMBEDDING_API_BASE_URL
    api_key = args.judge_api_key or settings.EMBEDDING_API_KEY
    if not api_key:
        raise SystemExit("没有裁判凭据：请给 --judge-api-key，或在 .env 里配 EMBEDDING_API_KEY")
    backoff = [float(item) for item in args.backoff.split(",") if item.strip()]
    judge = JudgeClient(
        model=args.judge_model,
        base_url=base_url,
        api_key=api_key,
        min_interval=args.min_interval,
        backoff=backoff,
        timeout=args.timeout,
    )

    started = time.monotonic()
    faith_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        for index, name in enumerate(faith_names, start=1):
            verdict = await judge_faithfulness_http(client, judge, by_name[name])
            faith_rows.append({"name": name, **verdict})
            print(
                f"[faith {index}/{len(faith_names)}] {name} "
                f"glm={glm_faith_rows[name].get('faithful')} {judge.model}={verdict.get('faithful')}",
                flush=True,
            )
        for index, case in enumerate(quality_cases, start=1):
            verdict = await judge_quality_http(client, judge, case)
            quality_rows.append({"name": case["name"], **verdict})
            print(
                f"[quality {index}/{len(quality_cases)}] {case['name']} "
                f"glm={case.get('llm_judge', {}).get('verdict')} {judge.model}={verdict.get('verdict')}",
                flush=True,
            )
    elapsed = round(time.monotonic() - started, 1)

    faithfulness_cases = []
    for row in faith_rows:
        glm_row = glm_faith_rows[row["name"]]
        glm_side = {
            "available": bool(glm_row.get("available")),
            "faithful": glm_row.get("faithful"),
            "unsupported": glm_row.get("unsupported") or [],
            "reason": glm_row.get("reason"),
        }
        judge_side = {
            "available": row["available"],
            "faithful": row["faithful"],
            "unsupported": row["unsupported"],
            "reason": row["reason"],
            "error": row.get("error"),
            "raw_excerpt": row.get("raw_excerpt"),
        }
        faithfulness_cases.append(
            {
                "name": row["name"],
                "evidence_chunks": len(_evidence_chunks(by_name[row["name"]])),
                "glm": glm_side,
                "judge": judge_side,
                "diff": _diff_faithfulness(glm_side, judge_side),
            }
        )

    quality_case_rows = []
    for case, row in zip(quality_cases, quality_rows, strict=True):
        glm_side = dict(case.get("llm_judge") or {})
        glm_side = {
            "available": bool(glm_side.get("available")),
            "groundedness": glm_side.get("groundedness"),
            "answer_relevance": glm_side.get("answer_relevance"),
            "completeness": glm_side.get("completeness"),
            "verdict": glm_side.get("verdict"),
            "reason": glm_side.get("reason"),
        }
        judge_side = {key: row.get(key) for key in (*_QUALITY_DIMS, "available", "verdict", "reason", "error", "raw_excerpt")}
        quality_case_rows.append(
            {"name": case["name"], "glm": glm_side, "judge": judge_side, **_diff_quality(glm_side, judge_side)}
        )

    faith_divergent = [row for row in faithfulness_cases if row["diff"]["divergent"]]
    quality_divergent = sorted(
        (row for row in quality_case_rows if row["divergent"]), key=lambda row: (-(row["max_delta"] or 0.0), row["name"])
    )
    dim_gap_names = sorted(
        row["name"] for row in quality_case_rows if (row["max_delta"] or 0.0) >= SCORE_STEP
    )
    glm_faith_summary = glm_faithfulness.get("summary") or {}
    glm_model = settings.LLM_MODEL
    report: dict[str, Any] = {
        "eval": "cross_model_judge",
        "generated_at": datetime.now(UTC).isoformat(),
        "judge": {
            "model": judge.model,
            "base_url": base_url,
            "provider": "openai_compatible",
            "credential_source": "cli"
            if (args.judge_base_url or args.judge_api_key)
            else "EMBEDDING_API_KEY / EMBEDDING_API_BASE_URL",
            "temperature": 0.0,
            "max_tokens": 1024,
        },
        "sources": {
            "result": args.result,
            "faithfulness": args.faithfulness,
            "result_evaluation_id": result.get("evaluation_id"),
            "glm_faithfulness_judge_model": glm_faithfulness.get("judge_model"),
        },
        "divergence_rule": {
            "score_step": SCORE_STEP,
            "quality": "任一维度 |Δscore| ≥ 0.1（一档 = 0.1 分）或 verdict 不同",
            "faithfulness": "faithful 布尔不同，或 unsupported 列表不同",
        },
        "run": {
            **judge.stats(),
            "elapsed_seconds": elapsed,
            "limit": args.limit or None,
            "faithfulness_batch": len(faith_names),
            "quality_batch": len(quality_cases),
        },
        "data_checks": {
            "faithfulness_names_joined": len(faith_names),
            "faithfulness_names_missing_in_result": missing_names,
            "faithfulness_batch_not_answerable": [
                name
                for name in faith_names
                if by_name[name].get("should_refuse") or not by_name[name].get("can_answer")
            ],
            "glm_faithfulness_report_rate": glm_faith_summary.get("faithfulness_rate"),
            "glm_faithfulness_report_faithful_cases": glm_faith_summary.get("faithful_cases"),
        },
        "faithfulness": {
            "batch": "faithfulness_report.json 覆盖的同一批答案（问题 + 答案 + 写进答案的引用片段）",
            "glm": {"model": glm_faithfulness.get("judge_model"), **glm_faith_summary},
            "judge": {"model": judge.model, **_faithfulness_summary(faith_rows)},
            "divergence_count": len(faith_divergent),
            "divergent_names": [row["name"] for row in faith_divergent],
            "cases": faithfulness_cases,
        },
        "quality": {
            "batch": "qa_eval_repaired.json 全部作答题目（35 题，含 4 题应拒答）",
            "glm": {"model": glm_model, **_quality_means([dict(case.get("llm_judge") or {}) for case in quality_cases])},
            "judge": {"model": judge.model, **_quality_means(quality_rows)},
            "dim_gap_threshold": SCORE_STEP,
            "dim_gap_count": len(dim_gap_names),
            "dim_gap_names": dim_gap_names,
            "divergence_count": len(quality_divergent),
            "verdict_change_count": sum(1 for row in quality_case_rows if row["verdict_changed"]),
            "cases": quality_case_rows,
        },
    }
    report["summary"] = {
        "faithfulness": {
            glm_model: {
                "rate": glm_faith_summary.get("faithfulness_rate"),
                "faithful": glm_faith_summary.get("faithful_cases"),
                "judged": glm_faith_summary.get("judged"),
            },
            judge.model: {
                "rate": report["faithfulness"]["judge"]["faithfulness_rate"],
                "faithful": report["faithfulness"]["judge"]["faithful_cases"],
                "judged": report["faithfulness"]["judge"]["judged"],
            },
            "divergent_cases": len(faith_divergent),
        },
        "quality": {
            glm_model: {field: report["quality"]["glm"][field] for field in _QUALITY_DIMS}
            | {"pass_rate": report["quality"]["glm"]["pass_rate"]},
            judge.model: {field: report["quality"]["judge"][field] for field in _QUALITY_DIMS}
            | {"pass_rate": report["quality"]["judge"]["pass_rate"]},
            "cases_with_dim_gap_ge_0_1": len(dim_gap_names),
            "cases_with_verdict_change": report["quality"]["verdict_change_count"],
            "divergent_cases": len(quality_divergent),
        },
    }
    return report


def _sheet_faithfulness_entry(number: int, row: dict[str, Any], case: dict[str, Any], other_label: str) -> list[str]:
    glm, judge = row["glm"], row["judge"]
    glm_label = "忠实" if glm["faithful"] else "不忠实"
    judge_label = "忠实" if judge["faithful"] else "不忠实"
    chunks = [item for item in (case.get("citations") or []) if item.get("source_text")][:_SHEET_EVIDENCE_ITEMS]
    evidence = " ／ ".join(
        f"`[片段 {index}] {_clip(item.get('source_text'), _SHEET_EVIDENCE_CHARS)}`"
        for index, item in enumerate(chunks, 1)
    )
    unsupported = "；".join(f"（{index}）{item}" for index, item in enumerate(judge.get("unsupported") or [], 1)) or "无"
    return [
        f"### {number}. {row['name']}｜忠实度分歧（faithful {glm['faithful']} → {judge['faithful']}）",
        f"<!-- meta：id=cross-{number:03d} kind=faithfulness glm={glm_label} {other_label}={judge_label} -->",
        "",
        f"- 问题：{_clip(case.get('question'), 300)}",
        f"- 系统答案：{_clip(case.get('answer'), _SHEET_ANSWER_CHARS)}",
        f"- 写进答案的引用片段（前 {len(chunks)} 条）：{evidence or '无'}",
        f"- **glm-5.2**：{glm_label}（faithful={glm['faithful']}）｜理由：{glm.get('reason') or '无'}",
        f"- **{other_label}**：{judge_label}（faithful={judge['faithful']}）"
        f"｜理由：{judge.get('reason') or '无'}｜不支持陈述：{unsupported}",
        f"- 差异：faithful 布尔不同 = {row['diff']['faithful_changed']}，unsupported 列表不同 = {row['diff']['unsupported_only']}",
        "- 人工结论：",
        "- 备注：",
        "",
    ]


def _sheet_quality_entry(number: int, row: dict[str, Any], case: dict[str, Any], other_label: str) -> list[str]:
    glm, judge = row["glm"], row["judge"]
    score_cells = "｜".join(f"{field} {glm[field]} → {judge[field]}（Δ{row['deltas'][field]}）" for field in _QUALITY_DIMS)
    headline = "，".join(
        f"{field} {glm[field]}→{judge[field]}" for field in _QUALITY_DIMS if (row["deltas"][field] or 0.0) >= SCORE_STEP
    )
    return [
        f"### {number}. {row['name']}｜质量分歧（{headline or 'verdict 变化'}）",
        f"<!-- meta：id=cross-{number:03d} kind=quality glm={glm['verdict']} {other_label}={judge['verdict']} -->",
        "",
        f"- 问题：{_clip(case.get('question'), 300)}",
        f"- 参考答案：{_clip(case.get('reference_answer'), _SHEET_REFERENCE_CHARS)}",
        f"- 系统答案：{_clip(case.get('answer'), _SHEET_ANSWER_CHARS)}",
        f"- 三维对照（glm → {other_label}）：{score_cells}｜verdict {glm['verdict']} → {judge['verdict']}",
        f"- **glm-5.2** 理由：{glm.get('reason') or '无'}",
        f"- **{other_label}** 理由：{judge.get('reason') or '无'}",
        "- 人工结论：",
        "- 备注：",
        "",
    ]


def build_sheet(
    report: dict[str, Any], cases: dict[str, dict[str, Any]], glm_label: str, other_label: str
) -> tuple[str, dict[str, Any]]:
    """只列两个口径判定不一致的题目：忠实度分歧全列，质量分歧按最大分差取前 30 条。"""
    faith_rows = [row for row in report["faithfulness"]["cases"] if row["diff"]["divergent"]]
    quality_rows = sorted(
        (row for row in report["quality"]["cases"] if row["divergent"]),
        key=lambda row: (-(row["max_delta"] or 0.0), row["name"]),
    )
    kept_quality = quality_rows[:_SHEET_MAX_QUALITY]
    dropped_quality = len(quality_rows) - len(kept_quality)
    faith_summary = report["summary"]["faithfulness"]
    quality_summary = report["summary"]["quality"]
    quality_judged = report["quality"]["glm"]["judged"]

    lines = [
        "# 交叉模型裁判分歧清单（人工抽检表）",
        "",
        f"> 生成：`scripts/run_cross_judge.py`（{report['generated_at']}）｜原始对照：`eval/outputs/cross_judge_report.json`",
        f"> 裁判 A = **{glm_label}**（原口径，同时是这批答案的生成模型）｜裁判 B = **{other_label}**（交叉口径，dashscope 兼容模式）",
        f"> 本次裁判覆盖：忠实度 `{report['run']['faithfulness_batch']}` 题（glm 原批 `{faith_summary[glm_label]['judged']}` 题）、"
        f"三维质量 `{report['run']['quality_batch']}` 题（glm 原批 `{quality_judged}` 题）；"
        f"分歧 {len(faith_rows)} + {len(quality_rows)} 条，本表列 {len(faith_rows)} + {len(kept_quality)} 条。",
        "",
        "## 填写说明",
        "",
        "- **人工结论** 一行只填三个选项之一：`正确` / `错误` / `无法判断`；`备注` 自由填。",
        "- 语义：忠实度条目里 `正确` = 该答案的事实性断言都能在片段里找到依据，`错误` = 存在查不到的断言；"
        "质量条目里 `正确` = 该答案质量可接受（可判 pass），`错误` = 不可接受。",
        "- `无法判断` 与留空**都不进一致率分母**；`scripts/score_human_review.py` 会把两个基准的分子/分母都打印出来。",
        f"- 分歧口径：质量维度任一 `|Δscore| ≥ {SCORE_STEP}`（一档 = {SCORE_STEP} 分）或 verdict 不同；"
        "忠实度看 `faithful` 布尔或 `unsupported` 列表不同。",
        f"- 本表**非盲测**：两位裁判看的是同一批答案与同一批引用片段，只换了模型；质量条目按最大分差降序，"
        f"被截掉 {dropped_quality} 条质量分歧。",
        "",
        "## 双方总览",
        "",
        f"| 指标 | {glm_label} | {other_label} |",
        "| --- | --- | --- |",
        f"| 忠实度率 | {faith_summary[glm_label]['rate']}（{faith_summary[glm_label]['faithful']}/{faith_summary[glm_label]['judged']}） "
        f"| {faith_summary[other_label]['rate']}（{faith_summary[other_label]['faithful']}/{faith_summary[other_label]['judged']}） |",
        f"| groundedness 均分 | {quality_summary[glm_label]['groundedness']} | {quality_summary[other_label]['groundedness']} |",
        f"| answer_relevance 均分 | {quality_summary[glm_label]['answer_relevance']} "
        f"| {quality_summary[other_label]['answer_relevance']} |",
        f"| completeness 均分 | {quality_summary[glm_label]['completeness']} | {quality_summary[other_label]['completeness']} |",
        f"| pass_rate | {quality_summary[glm_label]['pass_rate']} | {quality_summary[other_label]['pass_rate']} |",
        f"| 忠实度分歧题数 | \\- | {len(faith_rows)} |",
        f"| 三维差 ≥ {SCORE_STEP} 的题数 | \\- | {quality_summary['cases_with_dim_gap_ge_0_1']} |",
        "",
        "## 逐题分歧",
        "",
    ]
    number = 0
    for row in faith_rows:
        number += 1
        lines.extend(_sheet_faithfulness_entry(number, row, cases[row["name"]], other_label))
    for row in kept_quality:
        number += 1
        lines.extend(_sheet_quality_entry(number, row, cases[row["name"]], other_label))
    if dropped_quality:
        lines.extend(
            [
                f"> 另有 {dropped_quality} 条质量分歧未列入（按最大分差排序后截断，完整清单见 `eval/outputs/cross_judge_report.json`）。",
                "",
            ]
        )
    stats = {
        "entries_total": number,
        "faithfulness_entries": len(faith_rows),
        "quality_entries_kept": len(kept_quality),
        "quality_entries_dropped": dropped_quality,
        "quality_divergence_total": len(quality_rows),
    }
    return "\n".join(lines) + "\n", stats


def _sheet_evidence(case: dict[str, Any]) -> str:
    chunks = [item for item in (case.get("citations") or []) if item.get("source_text")][:_SHEET_EVIDENCE_ITEMS]
    return " ／ ".join(
        f"`[片段 {index}] {_clip(item.get('source_text'), _SHEET_EVIDENCE_CHARS)}`"
        for index, item in enumerate(chunks, 1)
    )


def _equal_interval(names: list[str], count: int) -> list[str]:
    """按 name 排序后等距取 `count` 条：同一输入两次结果完全一致（确定性抽样，不用随机数）。"""
    ordered = sorted(names)
    if count <= 0:
        return []
    if count >= len(ordered):
        return ordered
    step = len(ordered) / count
    picked: list[str] = []
    for index in range(count):
        candidate = ordered[min(len(ordered) - 1, int((index + 0.5) * step))]
        if candidate not in picked:
            picked.append(candidate)
    for name in ordered:  # 等距取在极窄分组里可能撞车，按序补齐
        if len(picked) >= count:
            break
        if name not in picked:
            picked.append(name)
    return picked


def _proportional_allocation(sizes: dict[Any, int], total: int) -> dict[Any, int]:
    """按各层规模比例分配 `total` 个名额（最大余数法；余数相同按层键排序，结果确定）。"""
    keys = sorted(sizes)
    grand = sum(sizes.values()) or 1
    quotas = {key: sizes[key] * total / grand for key in keys}
    allocation = {key: min(sizes[key], int(quotas[key])) for key in keys}
    for key in sorted(keys, key=lambda item: (-(quotas[item] - int(quotas[item])), item)):
        if sum(allocation.values()) >= total:
            break
        if allocation[key] < sizes[key]:
            allocation[key] += 1
    for key in keys:
        while sum(allocation.values()) < total and allocation[key] < sizes[key]:
            allocation[key] += 1
    return allocation


def sample_stratified(report: dict[str, Any], cases: dict[str, dict[str, Any]], sample_size: int) -> dict[str, Any]:
    """确定性分层抽样：分歧项全列（条数由数据决定），一致项抽 `sample_size` 条。

    `sample_size = 24` 时的构成写死在这里便于复核：
    - 忠实度 12 条：按 `document_id` 分层（本批 9001×13 / 9002×11 / 9003×7），按规模比例分到 5/4/3，层内等距取；
    - 质量 12 条：review 判罚全取（本批 1 条）→ 应拒答全取（本批 4 条）→ 其余从 pass 可答题等距取（7 条）。
    分歧项（本批 1 条 `plan_team_composition`）不占 `sample_size` 名额，另外计入表内。
    """
    faith_divergent = [row for row in report["faithfulness"]["cases"] if row["diff"]["divergent"]]
    quality_divergent = sorted(
        (row for row in report["quality"]["cases"] if row["divergent"]),
        key=lambda row: (-(row["max_delta"] or 0.0), row["name"]),
    )
    faith_agree = {row["name"]: row for row in report["faithfulness"]["cases"] if not row["diff"]["divergent"]}
    quality_agree = {row["name"]: row for row in report["quality"]["cases"] if not row["divergent"]}
    faith_quota = sample_size // 2
    quality_quota = sample_size - faith_quota

    doc_groups: dict[Any, list[str]] = {}
    for name in faith_agree:
        doc_groups.setdefault(cases[name].get("document_id"), []).append(name)
    doc_alloc = _proportional_allocation({doc: len(names) for doc, names in doc_groups.items()}, faith_quota)
    faith_picked: list[str] = []
    for doc in sorted(doc_groups, key=str):
        faith_picked.extend(_equal_interval(doc_groups[doc], doc_alloc.get(doc, 0)))
    faith_picked = faith_picked[:faith_quota]

    review_names = [name for name in sorted(quality_agree) if quality_agree[name]["glm"]["verdict"] != "pass"]
    refuse_names = [
        name for name in sorted(quality_agree) if cases[name].get("should_refuse") and name not in review_names
    ]
    excluded = set(review_names) | set(refuse_names)
    pass_names = [name for name in sorted(quality_agree) if name not in excluded]
    quality_picked = review_names[:2] + refuse_names[: min(4, max(1, quality_quota))]
    quality_picked += _equal_interval(pass_names, max(0, quality_quota - len(quality_picked)))
    quality_picked = quality_picked[:quality_quota]

    def kind_of(name: str) -> str:
        if quality_agree[name]["glm"]["verdict"] != "pass":
            return "review"
        return "refuse" if cases[name].get("should_refuse") else "pass"

    return {
        "faithfulness_divergent": faith_divergent,
        "quality_divergent": quality_divergent,
        "faithfulness_agree": faith_picked,
        "quality_agree": quality_picked,
        "doc_picked": {str(cases[name].get("document_id")): faith_picked.count(name) for name in faith_picked},
        "doc_available": {str(doc): len(names) for doc, names in doc_groups.items()},
        "quality_kinds_picked": {kind: sum(1 for name in quality_picked if kind_of(name) == kind) for kind in
                                 ("pass", "review", "refuse")},
        "quality_kinds_available": {kind: sum(1 for name in quality_agree if kind_of(name) == kind) for kind in
                                    ("pass", "review", "refuse")},
    }


def _strat_faithfulness_entry(
    number: int, row: dict[str, Any], case: dict[str, Any], item_label: str, glm_label: str, other_label: str
) -> list[str]:
    glm, judge = row["glm"], row["judge"]
    glm_verdict = "忠实" if glm["faithful"] else "不忠实"
    judge_verdict = "忠实" if judge["faithful"] else "不忠实"
    unsupported = "；".join(f"（{index}）{item}" for index, item in enumerate(judge.get("unsupported") or [], 1)) or "无"
    return [
        f"### {number}. {row['name']}｜忠实度·{item_label}",
        f"<!-- meta：id=cross-{number:03d} kind=faithfulness item={'divergent' if item_label == '分歧项' else 'agree'}"
        f" glm={glm_verdict} {other_label}={judge_verdict} -->",
        "",
        f"- 类型：忠实度｜本条目：{item_label}｜裁判判定：{glm_verdict}"
        f"（两位裁判{'一致' if glm_verdict == judge_verdict else '不一致'}）",
        f"- 问题：{_clip(case.get('question'), 300)}",
        f"- 系统答案：{_clip(case.get('answer'), _SHEET_ANSWER_CHARS)}",
        f"- 写进答案的引用片段（判「忠实」看这些）：{_sheet_evidence(case) or '无'}",
        "- 人工结论：",
        "- 备注：",
        "",
        "<details><summary>两位裁判意见（填完再看）</summary>",
        "",
        f"- **{glm_label}**：{glm_verdict}（faithful={glm['faithful']}）｜理由：{glm.get('reason') or '无'}",
        f"- **{other_label}**：{judge_verdict}（faithful={judge['faithful']}）｜理由：{judge.get('reason') or '无'}"
        f"｜不支持陈述：{unsupported}",
        f"- 差异：faithful 布尔不同 = {row['diff']['faithful_changed']}，unsupported 列表不同 = {row['diff']['unsupported_only']}",
        "",
        "</details>",
        "",
    ]


def _strat_quality_entry(
    number: int, row: dict[str, Any], case: dict[str, Any], item_label: str, glm_label: str, other_label: str
) -> list[str]:
    glm, judge = row["glm"], row["judge"]
    type_label = "质量·应拒答" if case.get("should_refuse") else "质量"
    cells = "｜".join(f"{field} {glm[field]} → {judge[field]}（Δ{row['deltas'][field]}）" for field in _QUALITY_DIMS)
    return [
        f"### {number}. {row['name']}｜{type_label}·{item_label}",
        f"<!-- meta：id=cross-{number:03d} kind=quality item={'divergent' if item_label == '分歧项' else 'agree'}"
        f" glm={glm['verdict']} {other_label}={judge['verdict']} -->",
        "",
        f"- 类型：{type_label}｜本条目：{item_label}｜裁判判定：{glm['verdict']}"
        f"（两位裁判{'判罚一致' if glm['verdict'] == judge['verdict'] else '判罚不一致'}）",
        f"- 问题：{_clip(case.get('question'), 300)}",
        f"- 参考答案：{_clip(case.get('reference_answer'), _SHEET_REFERENCE_CHARS)}",
        f"- 系统答案：{_clip(case.get('answer'), _SHEET_ANSWER_CHARS)}",
        f"- 写进答案的引用片段：{_sheet_evidence(case) or '无'}",
        "- 人工结论：",
        "- 备注：",
        "",
        "<details><summary>两位裁判意见（填完再看）</summary>",
        "",
        f"- 三维对照（{glm_label} → {other_label}）：{cells}｜verdict {glm['verdict']} → {judge['verdict']}",
        f"- **{glm_label}** 理由：{glm.get('reason') or '无'}",
        f"- **{other_label}** 理由：{judge.get('reason') or '无'}",
        "",
        "</details>",
        "",
    ]


def build_stratified_sheet(
    report: dict[str, Any], cases: dict[str, dict[str, Any]], glm_label: str, other_label: str, sample_size: int
) -> tuple[str, dict[str, Any]]:
    """分层抽样人工抽检表：分歧项全列 + 一致项确定性分层抽样（同一输入两次生成同一张表）。"""
    picked = sample_stratified(report, cases, sample_size)
    faith_rows = {row["name"]: row for row in report["faithfulness"]["cases"]}
    quality_rows = {row["name"]: row for row in report["quality"]["cases"]}
    faith_summary = report["summary"]["faithfulness"]
    quality_summary = report["summary"]["quality"]
    quality_judged = report["quality"]["glm"]["judged"]

    divergent_rows: list[tuple[str, dict[str, Any], str]] = [
        ("quality", row, "分歧项") for row in picked["quality_divergent"]
    ] + [("faithfulness", row, "分歧项") for row in picked["faithfulness_divergent"]]
    faith_agree_rows = [("faithfulness", faith_rows[name], "一致项") for name in picked["faithfulness_agree"]]
    quality_agree_rows = [("quality", quality_rows[name], "一致项") for name in picked["quality_agree"]]

    doc_cells = " / ".join(f"{doc}×{count}" for doc, count in sorted(picked["doc_picked"].items()))
    kind_cells = " / ".join(f"{kind} {picked['quality_kinds_picked'][kind]}" for kind in ("pass", "review", "refuse"))
    total = len(divergent_rows) + len(faith_agree_rows) + len(quality_agree_rows)
    lines = [
        "# 交叉模型裁判分层抽样表（人工抽检）",
        "",
        f"> 生成：`scripts/run_cross_judge.py --sheet-mode stratified`（{datetime.now(UTC).isoformat()}）｜"
        "本次生成 **0 次模型调用**（只读已有报告）",
        f"> 裁判 A = **{glm_label}**（原口径，同时是这批答案的生成模型）｜裁判 B = **{other_label}**（交叉口径，dashscope 兼容模式）",
        f"> 抽样来源 `eval/outputs/cross_judge_report.json`：忠实度 {report['run']['faithfulness_batch']} 题、"
        f"三维质量 {report['run']['quality_batch']} 题；分歧 {len(picked['faithfulness_divergent'])} + "
        f"{len(picked['quality_divergent'])} 条全部列入，另抽一致项 {sample_size} 条。",
        "",
        "## 怎么填",
        "",
        "- 每条的顺序：问题 → 参考答案 → 系统答案 → 引用片段 → `人工结论：` / `备注：`；"
        "两位裁判的判定与理由折叠在 `<details>` 里，**先自己判，填完再展开**。",
        "- **人工结论** 只填三个选项之一：`正确` / `错误` / `无法判断`；`备注` 自由填。",
        "- 语义：忠实度条目里 `正确` = 该答案的事实性断言都能在引用片段里找到依据；"
        "质量条目里 `正确` = 该答案质量可接受（可判 pass）。",
        "- `无法判断` 与留空**都不进一致率分母**；`scripts/score_human_review.py` 会打印两个基准的分子/分母与逐层进度。",
        "- 验收口径：人工与裁判同向的比例（分子 = 人工结论与该侧裁判判罚同向，分母 = 人工表态且该侧裁判有明确判罚的条目）。",
        "- 本表**非盲测**：两位裁判看的是同一批答案与同一批引用片段，只换了模型；抽样确定（按 name 排序后等距取），"
        "同一条命令两次生成同一张表。",
        "",
        "## 填写进度",
        "",
        "| 分层 | 条目 | 已填 | 未填 | 说明 |",
        "| --- | --- | --- | --- | --- |",
        f"| 分歧项 | {len(divergent_rows)} | 0 | {len(divergent_rows)} | 两位裁判判定不一致（维度差 ≥{SCORE_STEP} 档或 verdict 不同）的题全部列入 |",
        f"| 忠实度·一致项 | {len(faith_agree_rows)} | 0 | {len(faith_agree_rows)} | 分层取数：{doc_cells} |",
        f"| 质量·一致项 | {len(quality_agree_rows)} | 0 | {len(quality_agree_rows)} | 分层取数：{kind_cells} |",
        f"| 合计 | {total} | 0 | {total} | 跑 `score_human_review.py` 会把实际已填/未填逐层回显 |",
        "",
        "## 双方总览（全量口径，不是本表抽样）",
        "",
        f"| 指标 | {glm_label} | {other_label} |",
        "| --- | --- | --- |",
        f"| 忠实度率 | {faith_summary[glm_label]['rate']}（{faith_summary[glm_label]['faithful']}/{faith_summary[glm_label]['judged']}） "
        f"| {faith_summary[other_label]['rate']}（{faith_summary[other_label]['faithful']}/{faith_summary[other_label]['judged']}） |",
        f"| groundedness 均分 | {quality_summary[glm_label]['groundedness']} | {quality_summary[other_label]['groundedness']} |",
        f"| answer_relevance 均分 | {quality_summary[glm_label]['answer_relevance']} "
        f"| {quality_summary[other_label]['answer_relevance']} |",
        f"| completeness 均分 | {quality_summary[glm_label]['completeness']} | {quality_summary[other_label]['completeness']} |",
        f"| pass_rate | {quality_summary[glm_label]['pass_rate']} | {quality_summary[other_label]['pass_rate']} |",
        f"| 忠实度分歧题数 | \\- | {faith_summary['divergent_cases']} |",
        f"| 三维差 ≥ {SCORE_STEP} 的题数 | \\- | {quality_summary['cases_with_dim_gap_ge_0_1']} |",
        "",
    ]
    number = 0
    groups = [
        (f"## 一、分歧项（{len(divergent_rows)} 条：两位裁判判定不一致，全部列入）", divergent_rows),
        (f"## 二、忠实度·一致项抽样（{len(faith_agree_rows)} 条：{doc_cells}）", faith_agree_rows),
        (f"## 三、质量·一致项抽样（{len(quality_agree_rows)} 条：{kind_cells}）", quality_agree_rows),
    ]
    for title, rows in groups:
        lines.extend([title, ""])
        for kind, row, item_label in rows:
            number += 1
            case = cases[row["name"]]
            if kind == "faithfulness":
                lines.extend(_strat_faithfulness_entry(number, row, case, item_label, glm_label, other_label))
            else:
                lines.extend(_strat_quality_entry(number, row, case, item_label, glm_label, other_label))
    stats = {
        "mode": "stratified",
        "entries_total": number,
        "divergent_entries": len(divergent_rows),
        "faithfulness_agree_entries": len(faith_agree_rows),
        "quality_agree_entries": len(quality_agree_rows),
        "sheet_sample_requested": sample_size,
        "names": {
            "divergent": [row["name"] for _, row, _ in divergent_rows],
            "faithfulness_agree": [row["name"] for _, row, _ in faith_agree_rows],
            "quality_agree": [row["name"] for _, row, _ in quality_agree_rows],
        },
        "strata": {
            "faithfulness_docs_available": picked["doc_available"],
            "faithfulness_docs_picked": picked["doc_picked"],
            "quality_kinds_available": picked["quality_kinds_available"],
            "quality_kinds_picked": picked["quality_kinds_picked"],
        },
    }
    return "\n".join(lines) + "\n", stats


def build_stratified_outputs(args: argparse.Namespace) -> int:
    """stratified 模式：只读已有报告生成分层抽样表与构建自证 JSON；不构造 JudgeClient、不发任何请求。"""
    report_path = ROOT / args.from_report
    before = (report_path.stat().st_size, report_path.stat().st_mtime_ns)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    result = json.loads((ROOT / args.result).read_text(encoding="utf-8"))
    cases = {case["name"]: case for case in result["cases"]}
    glm_label = report["quality"]["glm"]["model"]
    sheet, stats = build_stratified_sheet(report, cases, glm_label, args.judge_model, args.sheet_sample)
    (ROOT / args.sheet_output).write_text(sheet, encoding="utf-8")
    meta_path = ROOT / (args.sheet_meta or Path(args.sheet_output).with_suffix(".build.json").name)
    after = (report_path.stat().st_size, report_path.stat().st_mtime_ns)
    meta = {
        "mode": "stratified",
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_rule": "分歧项全列；一致项按 document_id（忠实度）与 verdict/应拒答（质量）分层、层内按 name 等距取",
        "model_calls": 0,
        "judge_client_constructed": False,
        "source_report": {
            "path": args.from_report,
            "bytes": before[0],
            "read_only_unchanged": before == after,
            "written_by_this_run": False,
            "run": report["run"],
        },
        "sheet": {"path": args.sheet_output, "entries_total": stats["entries_total"]},
        **{key: value for key, value in stats.items() if key != "mode"},
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("=== 分层抽样人工抽检表（零模型调用） ===")
    print(f"输入报告：{args.from_report}（{before[0]} B，构建后未被改写：{before == after}）")
    print(
        f"源报告的请求数：attempts={report['run']['http_attempts']} ok={report['run']['ok_calls']} "
        f"retries={report['run']['retries']} 429={report['run']['rate_limit_429_hits']}"
    )
    print("本次模型调用：0（stratified 分支不构造 JudgeClient、不调 collect()，只读已有报告）")
    print(
        f"表内条目：分歧 {stats['divergent_entries']} + 忠实度一致项 {stats['faithfulness_agree_entries']} "
        f"+ 质量一致项 {stats['quality_agree_entries']} = {stats['entries_total']}"
    )
    print(
        f"忠实度分层：取数 {stats['strata']['faithfulness_docs_picked']}"
        f"｜候选 {stats['strata']['faithfulness_docs_available']}"
    )
    print(
        f"质量分层：取数 {stats['strata']['quality_kinds_picked']}"
        f"｜候选 {stats['strata']['quality_kinds_available']}"
    )
    print(f"表：{args.sheet_output}｜自证：{meta_path.relative_to(ROOT)}")
    return 0


def print_summary(report: dict[str, Any]) -> None:
    faith = report["summary"]["faithfulness"]
    quality = report["summary"]["quality"]
    judge_label = report["judge"]["model"]
    glm_label = report["quality"]["glm"]["model"]
    print("=== 交叉模型裁判一轮结果 ===")
    print(f"裁判：{judge_label} @ {report['judge']['base_url']}（凭据：{report['judge']['credential_source']}）")
    print(
        f"忠实度：{glm_label} {faith[glm_label]['rate']}（{faith[glm_label]['faithful']}/{faith[glm_label]['judged']}） vs "
        f"{judge_label} {faith[judge_label]['rate']}（{faith[judge_label]['faithful']}/{faith[judge_label]['judged']}）"
        f"｜分歧 {faith['divergent_cases']} 题"
    )
    print(
        f"三维均分（{glm_label} → {judge_label}）："
        + "｜".join(f"{field} {quality[glm_label][field]} → {quality[judge_label][field]}" for field in _QUALITY_DIMS)
        + f"｜pass_rate {quality[glm_label]['pass_rate']} → {quality[judge_label]['pass_rate']}"
    )
    print(
        f"逐题差异 ≥{SCORE_STEP} 档：{quality['cases_with_dim_gap_ge_0_1']} 题｜verdict 变化："
        f"{quality['cases_with_verdict_change']} 题"
    )
    names = report["quality"]["dim_gap_names"]
    print("差异 ≥1 档的题号：" + ("、".join(names) if names else "无"))
    print(
        f"请求：attempts={report['run']['http_attempts']} ok={report['run']['ok_calls']} "
        f"retries={report['run']['retries']} 429={report['run']['rate_limit_429_hits']} "
        f"failed={report['run']['failed_calls']}｜耗时 {report['run']['elapsed_seconds']}s"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="用非 glm 家族的裁判模型复判同一批答案，产出交叉口径报告")
    parser.add_argument("--result", default="eval/outputs/qa_eval_repaired.json")
    parser.add_argument("--faithfulness", default="eval/outputs/faithfulness_report.json")
    parser.add_argument("--judge-model", default="qwen-plus")
    parser.add_argument("--judge-base-url", default=None, help="默认取 .env 的 EMBEDDING_API_BASE_URL")
    parser.add_argument("--judge-api-key", default=None, help="默认取 .env 的 EMBEDDING_API_KEY")
    parser.add_argument("--min-interval", type=float, default=10.0, help="两次裁判请求的最小间隔秒数（默认 10）")
    parser.add_argument("--backoff", default="30,60,90,120,180", help="429/5xx 的退避秒数序列")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--limit", type=int, default=0, help="调试：每个通道只跑前 N 题（0 = 全跑）")
    parser.add_argument("--output", default=None, help="交叉口径报告 JSON；裁判模式必填，stratified 模式不写")
    parser.add_argument("--sheet-output", default="divergence_review_sheet.md", help="人工抽检表 markdown 路径")
    parser.add_argument(
        "--sheet-mode",
        choices=("divergence", "stratified"),
        default="divergence",
        help="divergence＝只列分歧（默认，保持既有行为）；stratified＝分层抽样表（零模型调用，只读已有报告）",
    )
    parser.add_argument(
        "--sheet-sample",
        type=int,
        default=0,
        help="stratified 抽样的「一致项」条数（分歧项另计、不占名额；0 = 不抽样）",
    )
    parser.add_argument(
        "--from-report",
        default="eval/outputs/cross_judge_report.json",
        help="stratified 的输入报告（只读，不会被改写）",
    )
    parser.add_argument("--sheet-meta", default=None, help="分层抽样表的构建自证 JSON（默认 <sheet>.build.json）")
    parser.add_argument("--no-sheet", action="store_true", help="只出 JSON，不生成人工抽检表")
    args = parser.parse_args()

    if args.sheet_mode == "stratified" or args.sheet_sample > 0:
        return build_stratified_outputs(args)
    if not args.output:
        parser.error("裁判模式必须给 --output（例如 --output cross_judge_report.json）")

    report = asyncio.run(collect(args))
    if not args.no_sheet:
        result = json.loads((ROOT / args.result).read_text(encoding="utf-8"))
        cases = {case["name"]: case for case in result["cases"]}
        glm_label = report["quality"]["glm"]["model"]
        sheet, sheet_stats = build_sheet(report, cases, glm_label, args.judge_model)
        report["sheet"] = {"path": args.sheet_output, **sheet_stats}
        (ROOT / args.sheet_output).write_text(sheet, encoding="utf-8")
    (ROOT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
