"""Build a small, reproducible evaluation bundle from seeded demo data."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.database import SessionLocal
from app.models.document import Document
from app.models.user import User


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "demo@ai-office.example.com").first()
        if not user:
            raise RuntimeError("请先执行 python scripts/seed_demo_data.py")
        contract = db.query(Document).filter(Document.user_id == user.id, Document.title == "知识库升级实施合同（演示）").first()
        plan = db.query(Document).filter(Document.user_id == user.id, Document.title == "知识库升级项目计划（演示）").first()
        if not contract or not plan:
            raise RuntimeError("演示文档不完整")
        bundle = ROOT_DIR / "eval" / "bundles" / "demo_office"
        docs = bundle / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        contract_target = docs / "demo_contract.md"; plan_target = docs / "demo_project_plan.md"
        contract_target.write_text(Path(contract.file_path).read_text(encoding="utf-8"), encoding="utf-8")
        plan_target.write_text(Path(plan.file_path).read_text(encoding="utf-8"), encoding="utf-8")
        cases = [
            ("contract_date", contract, "合同中的项目上线日期是什么？", ["2026 年 8 月 1 日"]),
            ("contract_amount", contract, "合同总金额是多少？", ["268 万元"]),
            ("plan_date", plan, "项目计划中的上线时间是什么？", ["2026 年 8 月 15 日"]),
            ("plan_budget", plan, "项目计划的预算是多少？", ["268 万元"]),
        ]
        dataset = [{"name": name, "document_id": doc.id, "document_name": doc.title, "category": "demo_fact", "question": question, "reference_answer": keys[0], "expected_chunk_keywords": keys, "expected_answer_keywords": keys, "should_refuse": False} for name, doc, question, keys in cases]
        dataset += [{"name": "contract_refusal", "document_id": contract.id, "document_name": contract.title, "category": "refusal", "question": "合同中约定的违约金比例是多少？", "reference_answer": "", "expected_chunk_keywords": [], "should_refuse": True}, {"name": "plan_refusal", "document_id": plan.id, "document_name": plan.title, "category": "refusal", "question": "项目计划中的负责人是谁？", "reference_answer": "", "expected_chunk_keywords": [], "should_refuse": True}]
        (bundle / "corpus_manifest.json").write_text(json.dumps([{"document_id": contract.id, "document_name": contract.title, "user_id": user.id, "file_path": str(contract_target.relative_to(ROOT_DIR)).replace("\\", "/"), "file_type": "md"}, {"document_id": plan.id, "document_name": plan.title, "user_id": user.id, "file_path": str(plan_target.relative_to(ROOT_DIR)).replace("\\", "/"), "file_type": "md"}], ensure_ascii=False, indent=2), encoding="utf-8")
        (bundle / "qa_dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
        (bundle / "bundle_meta.json").write_text(json.dumps({"bundle_name": "demo_office", "purpose": "demo smoke evaluation; do not use its metrics in resume"}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"bundle_dir": str(bundle), "case_count": len(dataset), "document_ids": [contract.id, plan.id]}, ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
