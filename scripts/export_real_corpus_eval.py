"""#47 AI-1 真实语料评测集 v1：从生产库抽取脱敏真实记录，追加进冻结评测集。

数据源：
- legal_consultations：真实咨询（过滤非中文测试/乱码行，同题去重）
- legal_contract_reviews / legal_drafts：解密（enc:v2）后抽取，脱敏后转评测用例

输出：eval/generation_eval_dataset.json（version 2.0 -> 2.1，新增 real-* 用例，带来源标注）
用法：python -B scripts/export_real_corpus_eval.py [--apply]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.encryption import decrypt_text  # noqa: E402


def _is_meaningful_cn(text: str) -> bool:
    if not text or len(text) < 6:
        return False
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    return cn / max(len(text), 1) >= 0.5


_NAME_RE = re.compile(r"(?<=[\u4e00-\u9fff])(张三|李四|王五|赵六|孙七|周八|吴九|郑十)(?=[\u4e00-\u9fff，。；、\s]|$)")


def _redact(text: str) -> str:
    """脱敏（阶段 4 扩展）：走 eval/redact 规则（姓名/手机/证件/金额/邮箱/案号/律所名）。"""
    from eval.redact import redact_pii

    return redact_pii(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export real corpus eval cases")
    parser.add_argument("--apply", action="store_true", help="Apply changes to generation_eval_dataset.json (default: dry-run)")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print(json.dumps({"status": "error", "message": "DATABASE_URL is not set"}))
        return 2

    engine = create_engine(database_url, pool_pre_ping=True)
    real_cases = {"consultation": [], "contract_review": [], "draft_generation": []}
    seen_questions = set()

    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, category, question, known_facts_json, missing_facts_json, risk_level FROM legal_consultations ORDER BY id"))
        for r in rows:
            question = _redact(str(r.question or ""))
            if not _is_meaningful_cn(question):
                continue
            norm = re.sub(r"\s+", "", question)[:40]
            if norm in seen_questions:
                continue
            seen_questions.add(norm)
            try:
                missing = json.loads(r.missing_facts_json) if r.missing_facts_json else []
            except Exception:  # noqa: BLE001
                missing = []
            real_cases["consultation"].append({
                "id": f"real-co-{r.id:04d}",
                "question": question,
                "source": "production",
                "gold": {
                    "expected_category": r.category or "other",
                    "citation_must_match_patterns": [],
                    "must_not_fabricate_winrate": True,
                    "must_have_missing_facts": bool(missing),
                    "risk_level_min": (r.risk_level or "low").lower(),
                },
            })

        reviews = conn.execute(text("SELECT id, title, content FROM legal_contract_reviews ORDER BY id"))
        for r in reviews:
            content = decrypt_text(str(r.content or "")) if str(r.content or "").startswith("enc:") else str(r.content or "")
            content = _redact(content)
            if not _is_meaningful_cn(content):
                continue
            real_cases["contract_review"].append({
                "id": f"real-cr-{r.id:04d}",
                "category": "regression",
                "description": f"production contract review #{r.id} ({_redact(str(r.title or ''))})",
                "contract_text": content,
                "gold": {"structural_only": True, "must_not_fabricate_entities": []},
            })

        drafts = conn.execute(text("SELECT id, document_type, title, fields_json FROM legal_drafts ORDER BY id"))
        for r in drafts:
            fields = {}
            try:
                fields = json.loads(r.fields_json) if r.fields_json else {}
            except Exception:  # noqa: BLE001
                fields = {}
            if not fields:
                continue
            required = [k for k in fields.keys() if str(fields[k] or "").strip() and not str(fields[k]).startswith("【")]
            if not required:
                continue
            real_cases["draft_generation"].append({
                "id": f"real-dg-{r.id:04d}",
                "document_type": r.document_type or "supplementary_agreement",
                "category": "complete",
                "description": f"production draft #{r.id}",
                "fields": {_redact(str(k)): _redact(str(v)) for k, v in fields.items()},
                "missing_fields": [],
                "gold": {
                    "required_fields_must_appear": [_redact(str(k)) for k in required],
                    "placeholder_fields": [],
                    "must_not_fabricate": [],
                    "must_contain_disclaimer": True,
                },
            })
    engine.dispose()

    counts = {k: len(v) for k, v in real_cases.items()}
    print(json.dumps({"status": "dry-run" if not args.apply else "applied", "extracted": counts, "samples": {
        "consultation": real_cases["consultation"][:2],
        "contract_review": [{"id": c["id"], "len": len(c["contract_text"])} for c in real_cases["contract_review"][:2]],
        "draft_generation": [{"id": c["id"], "type": c["document_type"], "fields": len(c["fields"])} for c in real_cases["draft_generation"][:2]],
    }}, ensure_ascii=False, indent=2))
    if counts["consultation"] + counts["contract_review"] + counts["draft_generation"] == 0:
        print("no real cases extracted; nothing to apply")
        return 0

    if not args.apply:
        print("dry-run: pass --apply to write into eval/generation_eval_dataset.json")
        return 0

    dataset_path = ROOT / "eval/generation_eval_dataset.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset["version"] = "2.1"
    dataset["real_corpus"] = {
        "source": "production db (aibg), redacted",
        "extracted_at": __import__("datetime").datetime.now().isoformat(),
        "counts": counts,
    }
    for section, cases in real_cases.items():
        existing_ids = {c["id"] for c in dataset[f"{section}_cases"]}
        added = [c for c in cases if c["id"] not in existing_ids]
        dataset[f"{section}_cases"].extend(added)
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "applied", "version": dataset["version"], "added": {k: len(v) for k, v in real_cases.items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
