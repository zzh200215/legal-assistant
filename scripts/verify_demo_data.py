"""Verify the local demo fixture without starting API services."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.database import SessionLocal
from app.models.document import Document, DocumentChunk
from app.models.meeting import Meeting, MeetingSummary
from app.models.task import Task
from app.models.user import User


def main() -> int:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "demo@ai-office.example.com").first()
        if not user:
            raise RuntimeError("演示账号不存在，请先运行 python scripts/seed_demo_data.py")
        contract = db.query(Document).filter(Document.user_id == user.id, Document.title == "知识库升级实施合同（演示）").first()
        plan = db.query(Document).filter(Document.user_id == user.id, Document.title == "知识库升级项目计划（演示）").first()
        meeting = db.query(Meeting).filter(Meeting.user_id == user.id, Meeting.title == "上线风险评审会（演示）").first()
        if not all([contract, plan, meeting]):
            raise RuntimeError("演示文档或会议不完整，请重新运行 seed_demo_data.py")
        summary = db.query(MeetingSummary).filter(MeetingSummary.meeting_id == meeting.id).first()
        task = db.query(Task).filter(Task.user_id == user.id, Task.title == "确认上线基线（演示）").first()
        if not summary or not task or not db.query(DocumentChunk).filter(DocumentChunk.document_id == contract.id).first():
            raise RuntimeError("演示依赖对象不完整，请重新运行 seed_demo_data.py")
        print(json.dumps({
            "valid": True,
            "account": "demo@ai-office.example.com",
            "demo_goals": [
                f"核对文档 {contract.id} 与文档 {plan.id} 的日期、金额和负责人冲突",
                f"总结会议 {meeting.id} 并将行动项创建为任务",
                f"核对文档 {contract.id} 与会议 {meeting.id} 的风险",
            ],
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
