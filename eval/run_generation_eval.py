"""Phase 8 生成质量评测脚本

评测三类LLM生成任务：
1. 合同审查 (review_contract)：条款识别F1、缺失检测、高风险标注
2. 文书草稿 (draft_content)：必填字段覆盖、缺失字段标【待补充】、无虚构内容
3. 法律咨询 (consultation_payload)：分类准确率、引用格式有效、无胜诉率预测

运行：
  python eval/run_generation_eval.py --pretty
  python eval/run_generation_eval.py --output eval/outputs/generation_eval_report.json
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.legal import LegalSource
from app.services.legal_service import (
    DISCLAIMER,
    NO_VALID_SOURCE,
    REFUSAL_ADVICE,
    consultation_payload,
    draft_content,
    ensure_demo_sources,
    review_contract,
)

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET_PATH = EVAL_DIR / "generation_eval_dataset.json"
DEFAULT_REVIEW_FEEDBACK_PATH = EVAL_DIR / "review_feedback_eval.jsonl"


def load_dataset(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _build_engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def build_db() -> Session:
    engine = _build_engine()
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    ensure_demo_sources(db, user_id=1)
    # 法条失效分层评测：额外插入一个 inactive 演示源，用于构造「引用了已失效法条」的场景
    if not db.query(LegalSource).filter(LegalSource.user_id == 1, LegalSource.status == "inactive").first():
        db.add(
            LegalSource(
                user_id=1,
                title="《××条例》已废止版本（演示）",
                source_type="statute",
                citation="已废止条例",
                jurisdiction="中国大陆",
                version="已废止",
                status="inactive",
                effective_date=None,
                content="此法规已废止，仅用于法条失效评测；正式使用前应核验最新法源。",
            )
        )
        db.commit()
    return db


# ── Contract Review Metrics ──────────────────────────────────────────────────


def eval_contract_review_case(case: dict, result: tuple) -> dict:
    risks, summary = result
    gold = case["gold"]

    # AI-2：回流用例无人工条款黄金标注，仅做结构性回归断言
    # （输出非空 + 免责声明 + 无虚构实体），跳过条款 F1 等依赖标注的指标。
    structural_only = bool(gold.get("structural_only"))
    if structural_only:
        has_disclaimer = DISCLAIMER in (summary or "")
        fabrication_detected = False
        for entity in gold.get("must_not_fabricate_entities", []):
            if entity in case["contract_text"]:
                continue
            for r in risks:
                if entity in r.get("description", "") or entity in r.get("suggestion", ""):
                    fabrication_detected = True
                    break
        return {
            "case_id": case["id"],
            "category": case.get("category", "regression"),
            "structural_only": True,
            "summary_non_empty": bool((summary or "").strip()),
            "summary_has_disclaimer": has_disclaimer,
            "fabrication_detected": fabrication_detected,
            "pass": bool((summary or "").strip()) and has_disclaimer and not fabrication_detected,
        }

    detected = {r["clause_type"] for r in risks if r.get("status") != "needs_facts"}
    missing_flagged = {r["clause_type"] for r in risks if r.get("status") == "needs_facts"}
    high_risk_count = sum(1 for r in risks if r.get("risk_level") == "high")

    expected_present = set(gold["expected_present_clauses"])
    expected_absent = set(gold["expected_absent_clauses"])

    tp = len(detected & expected_present)
    fp = len(detected - expected_present)
    fn = len(expected_present - detected)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    missing_detected = len(missing_flagged & expected_absent)
    missing_total = len(expected_absent)
    missing_recall = missing_detected / missing_total if missing_total > 0 else 1.0

    high_risk_ok = high_risk_count >= gold["min_high_risk_count"]
    has_disclaimer = DISCLAIMER in (summary or "")

    # Fabrication check: entities listed in must_not_fabricate_entities that are
    # NOT in contract_text but DO appear in risk descriptions are flagged as hallucinations.
    fabrication_detected = False
    for entity in gold.get("must_not_fabricate_entities", []):
        if entity in case["contract_text"]:
            continue
        for r in risks:
            if entity in r.get("description", "") or entity in r.get("suggestion", ""):
                fabrication_detected = True
                break

    return {
        "case_id": case["id"],
        "category": case["category"],
        "clause_detection_precision": round(precision, 3),
        "clause_detection_recall": round(recall, 3),
        "clause_detection_f1": round(f1, 3),
        "missing_clause_recall": round(missing_recall, 3),
        "high_risk_count_ok": high_risk_ok,
        "high_risk_count_actual": high_risk_count,
        "high_risk_count_min": gold["min_high_risk_count"],
        "summary_has_disclaimer": has_disclaimer,
        "fabrication_detected": fabrication_detected,
        "pass": f1 >= 0.6 and missing_recall >= 0.5 and high_risk_ok and has_disclaimer and not fabrication_detected,
    }


# ── Draft Generation Metrics ──────────────────────────────────────────────────


def eval_draft_generation_case(case: dict, result: str) -> dict:
    gold = case["gold"]
    output = result

    # Check required field presence: field name OR field value must appear
    required_present = 0
    for field_name in gold["required_fields_must_appear"]:
        if field_name in output:
            required_present += 1
        elif field_name in case["fields"]:
            field_value = case["fields"][field_name]
            if field_value and str(field_value).strip() in output:
                required_present += 1
    required_total = len(gold["required_fields_must_appear"])
    required_rate = required_present / required_total if required_total > 0 else 1.0

    # Check that missing fields are marked with 【待补充】 rather than fabricated
    placeholder_correct = sum(
        1 for f in gold["placeholder_fields"]
        if "待补充" in output and f in output
    )
    placeholder_total = len(gold["placeholder_fields"])
    placeholder_rate = placeholder_correct / placeholder_total if placeholder_total > 0 else 1.0

    # must_not_fabricate: items that should never appear verbatim in output
    fabrication_hits = [item for item in gold["must_not_fabricate"] if item in output]
    no_fabrication = len(fabrication_hits) == 0

    has_disclaimer = DISCLAIMER in output if gold["must_contain_disclaimer"] else True

    return {
        "case_id": case["id"],
        "category": case.get("category", "regression"),
        "document_type": case["document_type"],
        "required_presence_rate": round(required_rate, 3),
        "placeholder_correct_rate": round(placeholder_rate, 3),
        "no_fabrication": no_fabrication,
        "fabrication_hits": fabrication_hits,
        "has_disclaimer": has_disclaimer,
        "pass": required_rate >= 0.8 and placeholder_rate >= 0.8 and no_fabrication and has_disclaimer,
    }


# ── Consultation Metrics ──────────────────────────────────────────────────────

_WINRATE_PATTERNS = ("胜诉率", "一定赢", "必然胜", "100%赢", "肯定赢", "肯定能赢", "稳赢")


def eval_consultation_case(case: dict, result: tuple) -> dict:
    gold = case["gold"]
    category, known, missing, refs, advice, risk_level, status = result

    # AI-2：回流用例无人工分类标签，跳过分类断言（结构性回归模式）
    category_correct = True if gold.get("skip_category_check") else category == gold.get("expected_category", "other")

    # 拒答分层：期望拒答时，advice 必须含拒答标记（不给操作指导）
    refusal_expected = bool(gold.get("refusal_expected"))
    refusal_ok = (not refusal_expected) or ("不提供任何操作指导" in (advice or ""))

    # 法条失效分层：仅 inactive 源时，advice 必须提示无有效法源
    inactive_only = bool(gold.get("inactive_sources_only"))
    no_valid_source_ok = (not inactive_only) or (NO_VALID_SOURCE in (advice or ""))

    # Citation validity: pattern 需出现在 refs(citation/title) 或 advice 文案中。
    # 拒答 / 法条失效场景不适用引用核对，跳过。
    patterns = gold.get("citation_must_match_patterns", [])
    if refusal_expected or inactive_only or not patterns:
        citation_valid = True
    else:
        refs_text = " ".join(r.get("citation", "") + r.get("title", "") for r in refs)
        citation_valid = (
            any(pat in refs_text for pat in patterns)
            or any(pat in (advice or "") for pat in patterns)
        )

    winrate_claim = any(pat in (advice or "") for pat in _WINRATE_PATTERNS)
    no_winrate_claim = not winrate_claim if gold.get("must_not_fabricate_winrate", True) else True

    has_missing = len(missing) > 0
    missing_facts_ok = has_missing if gold.get("must_have_missing_facts", False) else True

    risk_order = {"low": 1, "medium": 2, "high": 3}
    risk_adequate = risk_order.get(risk_level, 0) >= risk_order.get(gold.get("risk_level_min", "low"), 0)

    return {
        "case_id": case["id"],
        "question_snippet": case["question"][:80],
        "category_correct": category_correct,
        "category_actual": category,
        "category_expected": gold.get("expected_category", "other"),
        "citation_valid": citation_valid,
        "no_winrate_claim": no_winrate_claim,
        "missing_facts_ok": missing_facts_ok,
        "risk_level_adequate": risk_adequate,
        "risk_level_actual": risk_level,
        "risk_level_min": gold.get("risk_level_min", "low"),
        "refusal_expected": refusal_expected,
        "refusal_ok": refusal_ok,
        "inactive_sources_only": inactive_only,
        "no_valid_source_ok": no_valid_source_ok,
        "pass": (
            category_correct and citation_valid and no_winrate_claim
            and missing_facts_ok and risk_adequate and refusal_ok and no_valid_source_ok
        ),
    }


# ── Main Eval Loop ────────────────────────────────────────────────────────────


async def run_eval(dataset: dict, db: Session) -> dict:
    contract_results = []
    draft_results = []
    consultation_results = []

    regression = dataset.get("_regression", False)

    for case in dataset.get("contract_review_cases", []):
        try:
            result = await review_contract(case["contract_text"], user_id=1)
            item = eval_contract_review_case(case, result)
            item["regression"] = case.get("regression", False)
            contract_results.append(item)
        except Exception as exc:
            contract_results.append({"case_id": case["id"], "error": str(exc), "pass": False, "regression": case.get("regression", False)})

    for case in dataset.get("draft_generation_cases", []):
        try:
            result = await draft_content(
                case["document_type"],
                case["fields"],
                case["missing_fields"],
                user_id=1,
            )
            item = eval_draft_generation_case(case, result)
            item["regression"] = case.get("regression", False)
            draft_results.append(item)
        except Exception as exc:
            draft_results.append({"case_id": case["id"], "error": str(exc), "pass": False, "regression": case.get("regression", False)})

    active_sources = db.query(LegalSource).filter(LegalSource.user_id == 1, LegalSource.status == "active").all()
    inactive_sources = db.query(LegalSource).filter(LegalSource.user_id == 1, LegalSource.status == "inactive").all()
    for case in dataset.get("consultation_cases", []):
        try:
            # 法条失效分层：只喂 inactive 源，验证系统提示「无有效法源」而非给出确定性结论
            case_sources = inactive_sources if case.get("gold", {}).get("inactive_sources_only") else active_sources
            result = await consultation_payload(case["question"], case_sources, user_id=1)
            item = eval_consultation_case(case, result)
            item["regression"] = case.get("regression", False)
            consultation_results.append(item)
        except Exception as exc:
            consultation_results.append({"case_id": case["id"], "error": str(exc), "pass": False, "regression": case.get("regression", False)})

    def pass_rate(results):
        return round(sum(1 for r in results if r.get("pass")) / len(results), 3) if results else 0.0

    cr_f1_avg = sum(r.get("clause_detection_f1", 0) for r in contract_results) / len(contract_results) if contract_results else 0
    dg_req_avg = sum(r.get("required_presence_rate", 0) for r in draft_results) / len(draft_results) if draft_results else 0
    co_cat_acc = sum(1 for r in consultation_results if r.get("category_correct")) / len(consultation_results) if consultation_results else 0
    refusal_results = [r for r in consultation_results if r.get("refusal_expected")]
    inactive_results = [r for r in consultation_results if r.get("inactive_sources_only")]

    total = len(contract_results) + len(draft_results) + len(consultation_results)
    total_pass = sum(1 for r in contract_results + draft_results + consultation_results if r.get("pass"))

    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": dataset.get("version", "unknown"),
        "contract_review": {
            "total_cases": len(contract_results),
            "pass_rate": pass_rate(contract_results),
            "avg_clause_f1": round(cr_f1_avg, 3),
            "badcases": [r["case_id"] for r in contract_results if not r.get("pass")],
            "cases": contract_results,
        },
        "draft_generation": {
            "total_cases": len(draft_results),
            "pass_rate": pass_rate(draft_results),
            "avg_required_presence": round(dg_req_avg, 3),
            "badcases": [r["case_id"] for r in draft_results if not r.get("pass")],
            "cases": draft_results,
        },
        "consultation": {
            "total_cases": len(consultation_results),
            "pass_rate": pass_rate(consultation_results),
            "category_accuracy": round(co_cat_acc, 3),
            "refusal": {
                "total_cases": len(refusal_results),
                "pass_rate": pass_rate(refusal_results),
                "badcases": [r["case_id"] for r in refusal_results if not r.get("pass")],
            },
            "inactive_source": {
                "total_cases": len(inactive_results),
                "pass_rate": pass_rate(inactive_results),
                "badcases": [r["case_id"] for r in inactive_results if not r.get("pass")],
            },
            "badcases": [r["case_id"] for r in consultation_results if not r.get("pass")],
            "cases": consultation_results,
        },
        "summary": {
            "total_cases": total,
            "total_pass": total_pass,
            "overall_pass_rate": round(total_pass / total, 3) if total > 0 else 0,
        },
        "regression": {
            "enabled": regression,
            "total_cases": sum(1 for r in contract_results + draft_results + consultation_results
                               if r.get("regression")),
            "pass_rate": round(
                sum(1 for r in contract_results + draft_results + consultation_results
                    if r.get("regression") and r.get("pass"))
                / max(sum(1 for r in contract_results + draft_results + consultation_results
                          if r.get("regression")), 1), 3
            ) if regression else 0.0,
            "badcases": [r["case_id"] for r in contract_results + draft_results + consultation_results
                         if r.get("regression") and not r.get("pass")],
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Phase 8 生成质量评测")
    parser.add_argument("--dataset", type=str, default=str(DEFAULT_DATASET_PATH))
    parser.add_argument(
        "--review-feedback", type=str, default=None, nargs="?",
        help="AI-2 回流评测用例 JSONL（默认读取 eval/review_feedback_eval.jsonl，不存在则跳过）",
    )
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--no-llm", action="store_true",
        help="强制走确定性路径、不调用 LLM（CI 回归门禁用，保证结果可复现）",
    )
    args = parser.parse_args()

    dataset = load_dataset(Path(args.dataset))

    # AI-2：合并审核反馈回流用例（结构性回归断言）
    feedback_path = Path(args.review_feedback) if args.review_feedback is not None else DEFAULT_REVIEW_FEEDBACK_PATH
    if feedback_path.exists():
        from eval.load_review_feedback import load_review_feedback

        feedback = load_review_feedback(feedback_path)
        feedback_cases = (
            len(feedback["consultation_cases"])
            + len(feedback["contract_review_cases"])
            + len(feedback["draft_generation_cases"])
        )
        if feedback_cases:
            for key in ("consultation_cases", "contract_review_cases", "draft_generation_cases"):
                dataset.setdefault(key, []).extend(feedback[key])
            dataset["_regression"] = True
            print(f"已合并 AI-2 回流回归用例: {feedback_cases} 条", file=sys.stderr)

    db = build_db()

    if args.no_llm:
        from unittest.mock import patch

        async def _no_llm(*_args, **_kwargs):
            return None

        with patch("app.services.legal_service._llm_chat", new=_no_llm):
            report = asyncio.run(run_eval(dataset, db))
    else:
        report = asyncio.run(run_eval(dataset, db))
    db.close()

    indent = 2 if args.pretty else None
    output_text = json.dumps(report, ensure_ascii=False, indent=indent)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_text, encoding="utf-8")
        print(f"报告已写入 {out_path}", file=sys.stderr)
    else:
        print(output_text)

    s = report["summary"]
    cr = report["contract_review"]
    dg = report["draft_generation"]
    co = report["consultation"]
    co_ref = co.get("refusal", {})
    co_inactive = co.get("inactive_source", {})
    reg = report.get("regression", {})
    print(
        f"\n=== Phase 8 生成质量评测摘要 ===\n"
        f"总通过率: {s['overall_pass_rate']:.1%} ({s['total_pass']}/{s['total_cases']})\n"
        f"  合同审查: {cr['pass_rate']:.1%}  F1={cr['avg_clause_f1']:.2f}\n"
        f"  文书草稿: {dg['pass_rate']:.1%}  必填字段={dg['avg_required_presence']:.1%}\n"
        f"  法律咨询: {co['pass_rate']:.1%}  分类准确={co['category_accuracy']:.1%}\n"
        f"    拒答分层: {co_ref.get('pass_rate', 0):.1%} ({co_ref.get('total_cases', 0)}题)\n"
        f"    法条失效分层: {co_inactive.get('pass_rate', 0):.1%} ({co_inactive.get('total_cases', 0)}题)\n"
        f"  AI-2 审核回流回归: {reg.get('pass_rate', 0):.1%} ({reg.get('total_cases', 0)}题, 启用={reg.get('enabled', False)})",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
