"""历史任务 case_id 回填（只补可证明的来源，默认 dry-run）。

背景：`tasks.case_id` 是 step79 才加的列。加列之前由文档/咨询/拆解产生的任务都没有案件，
案件任务视图只能看到新任务。本模块按**来源链路**补：

1. `source_type=legal_consultation` → `legal_consultations.case_id`；
2. `source_type=document` → `documents.metadata_json.case_id`（兼容 int / 数字字符串）；
3. 有 `parent_id` → 沿父任务链继承 `case_id`（支持多级拆解）。

不猜：meeting/chat 等没有可靠案件来源的任务保持为空；案件已删除的也不补（避免悬空引用）。

`apply=False` 只计算并返回报告，不修改任何行；`apply=True` 才写库并 commit。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.legal import LegalCase, LegalConsultation
from app.models.task import Task
from app.services.documents.case_link import parse_case_id

SOURCE_CONSULTATION = "legal_consultation"
SOURCE_DOCUMENT = "document"
MAX_PARENT_DEPTH = 12

REASON_COUNTERS = {
    "no_source": "skipped_no_source",
    "source_without_case": "skipped_source_without_case",
    "missing_source": "skipped_missing_source",
    "missing_case": "skipped_missing_case",
}


@dataclass
class TaskCaseBackfillReport:
    total_without_case: int = 0
    scanned: int = 0
    planned: int = 0
    applied: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)
    skipped_no_source: int = 0
    skipped_source_without_case: int = 0
    skipped_missing_source: int = 0
    skipped_missing_case: int = 0
    samples: list[dict] = field(default_factory=list)
    sample_limit: int = 20

    def add_sample(self, **payload) -> None:
        if len(self.samples) < self.sample_limit:
            self.samples.append(payload)

    def as_dict(self) -> dict:
        return {
            "total_without_case": self.total_without_case,
            "scanned": self.scanned,
            "planned": self.planned,
            "applied": self.applied,
            "by_rule": dict(sorted(self.by_rule.items())),
            "skipped": {
                "no_source": self.skipped_no_source,
                "source_without_case": self.skipped_source_without_case,
                "missing_source": self.skipped_missing_source,
                "missing_case": self.skipped_missing_case,
            },
            "samples": self.samples,
        }


def _direct_case(
    task: Task,
    *,
    consult_cases: dict[int, int | None],
    doc_meta: dict[int, str | None],
    case_ids: set[int],
) -> tuple[int | None, str | None]:
    """按来源对象直接拿案件；返回 (case_id, 跳过原因)。"""
    if task.source_type == SOURCE_CONSULTATION:
        if not task.source_id:
            return None, "no_source"
        if task.source_id not in consult_cases:
            return None, "missing_source"
        case_id = consult_cases[task.source_id]
        if not case_id:
            return None, "source_without_case"
        if case_id not in case_ids:
            return None, "missing_case"
        return case_id, None

    if task.source_type == SOURCE_DOCUMENT:
        if not task.source_id:
            return None, "no_source"
        if task.source_id not in doc_meta:
            return None, "missing_source"
        case_id = parse_case_id(doc_meta[task.source_id])
        if not case_id:
            return None, "source_without_case"
        if case_id not in case_ids:
            return None, "missing_case"
        return case_id, None

    return None, "no_source"


def _parent_case(
    db: Session,
    task: Task,
    *,
    assignments: dict[int, int],
    case_ids: set[int],
) -> tuple[int | None, str | None]:
    """沿 parent_id 向上找第一个有 case_id 的祖先；支持本轮已回填的父任务。"""
    parent_id = task.parent_id
    depth = 0
    while parent_id:
        if depth >= MAX_PARENT_DEPTH:
            return None, "missing_source"
        if parent_id in assignments:
            return assignments[parent_id], None
        row = db.query(Task.case_id, Task.parent_id).filter(Task.id == parent_id).first()
        if not row:
            return None, "missing_source"
        parent_case, next_parent = row
        if parent_case:
            if parent_case not in case_ids:
                return None, "missing_case"
            return parent_case, None
        parent_id = next_parent
        depth += 1
    return None, "source_without_case"


def backfill_task_case_ids(
    db: Session,
    *,
    apply: bool = False,
    limit: int | None = None,
    sample_limit: int = 20,
) -> TaskCaseBackfillReport:
    """扫描 `case_id IS NULL` 的任务并按来源链路回填；返回本次报告。"""
    report = TaskCaseBackfillReport(sample_limit=max(0, sample_limit))
    report.total_without_case = (
        db.query(func.count(Task.id)).filter(Task.case_id.is_(None)).scalar() or 0
    )

    query = db.query(Task).filter(Task.case_id.is_(None)).order_by(Task.id)
    if limit:
        query = query.limit(limit)
    tasks = query.all()
    report.scanned = len(tasks)
    if not tasks:
        return report

    case_ids = {row[0] for row in db.query(LegalCase.id).all()}

    consult_ids = {
        task.source_id
        for task in tasks
        if task.source_type == SOURCE_CONSULTATION and task.source_id
    }
    consult_cases: dict[int, int | None] = {}
    if consult_ids:
        consult_cases = dict(
            db.query(LegalConsultation.id, LegalConsultation.case_id)
            .filter(LegalConsultation.id.in_(consult_ids))
            .all()
        )

    doc_ids = {
        task.source_id for task in tasks if task.source_type == SOURCE_DOCUMENT and task.source_id
    }
    doc_meta: dict[int, str | None] = {}
    if doc_ids:
        doc_meta = dict(
            db.query(Document.id, Document.metadata_json)
            .filter(Document.id.in_(doc_ids))
            .all()
        )

    assignments: dict[int, int] = {}
    rules: dict[int, str] = {}
    reasons: dict[int, str] = {}

    for task in tasks:
        case_id, reason = _direct_case(
            task, consult_cases=consult_cases, doc_meta=doc_meta, case_ids=case_ids
        )
        if case_id:
            assignments[task.id] = case_id
            rules[task.id] = (
                "consultation" if task.source_type == SOURCE_CONSULTATION else "document"
            )
        else:
            reasons[task.id] = reason or "no_source"

    pending = [task for task in tasks if task.id not in assignments and task.parent_id]
    for _ in range(MAX_PARENT_DEPTH):
        if not pending:
            break
        progressed = False
        remaining: list[Task] = []
        for task in pending:
            case_id, reason = _parent_case(
                db, task, assignments=assignments, case_ids=case_ids
            )
            if case_id:
                assignments[task.id] = case_id
                rules[task.id] = "parent"
                progressed = True
            else:
                reasons[task.id] = reason or "missing_source"
                remaining.append(task)
        pending = remaining
        if not progressed:
            break

    for task in tasks:
        if task.id in assignments:
            report.planned += 1
            rule = rules[task.id]
            report.by_rule[rule] = report.by_rule.get(rule, 0) + 1
            report.add_sample(
                task_id=task.id,
                source_type=task.source_type,
                source_id=task.source_id,
                parent_id=task.parent_id,
                case_id=assignments[task.id],
                rule=rule,
            )
            if apply:
                task.case_id = assignments[task.id]
        else:
            counter = REASON_COUNTERS.get(
                reasons.get(task.id, "no_source"), "skipped_no_source"
            )
            setattr(report, counter, getattr(report, counter) + 1)

    if apply and assignments:
        db.commit()
        report.applied = len(assignments)
    return report
