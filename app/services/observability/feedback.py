"""反馈与 QA 复盘簇：QA 回放、反馈记录/统计、负反馈评估集导出。"""
import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.document import DocumentQARecord
from app.services.documents.document_qa_service import document_qa_service

settings = get_settings()


class FeedbackMixin:
    def list_qa_replays(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        page: int = 1,
        page_size: int = 20,
        source: str | None = None,
        feedback_status: str | None = None,
    ) -> dict:
        since = utc_now() - timedelta(days=days)
        query = db.query(DocumentQARecord).filter(DocumentQARecord.created_at >= since)
        if user_id is not None and not include_all_users:
            query = query.filter(DocumentQARecord.user_id == user_id)
        if source:
            query = query.filter(DocumentQARecord.source == source)
        if feedback_status:
            query = query.filter(DocumentQARecord.feedback_status == feedback_status)
        total = query.count()
        rows = (
            query.order_by(DocumentQARecord.created_at.desc(), DocumentQARecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        items = [document_qa_service.serialize_record(row) for row in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def list_feedback_records(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        limit: int = 200,
        feedback_value: str | None = None,
        feedback_status: str | None = None,
        source: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> list[DocumentQARecord] | tuple[list[DocumentQARecord], int]:
        since = utc_now() - timedelta(days=days)
        query = db.query(DocumentQARecord).filter(
            DocumentQARecord.feedback_created_at.isnot(None),
            DocumentQARecord.feedback_created_at >= since,
        )
        if user_id is not None and not include_all_users:
            query = query.filter(DocumentQARecord.user_id == user_id)
        if feedback_value:
            query = query.filter(DocumentQARecord.feedback_value == feedback_value)
        if feedback_status:
            query = query.filter(DocumentQARecord.feedback_status == feedback_status)
        if source:
            query = query.filter(DocumentQARecord.source == source)
        ordered = (
            query.order_by(DocumentQARecord.feedback_created_at.desc(), DocumentQARecord.id.desc())
        )
        if page is not None and page_size is not None:
            total = query.count()
            items = ordered.offset((page - 1) * page_size).limit(page_size).all()
            return items, total
        return ordered.limit(limit).all()

    def get_feedback_stats(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
        feedback_value: str | None = None,
        feedback_status: str | None = None,
        source: str | None = None,
    ) -> dict:
        rows = self.list_feedback_records(
            db=db,
            user_id=user_id,
            include_all_users=include_all_users,
            days=days,
            limit=2000,
            feedback_value=feedback_value,
            feedback_status=feedback_status,
            source=source,
        )

        by_value: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        by_source: dict[str, int] = {}
        by_date: dict[str, int] = {}
        resolved_count = 0
        open_count = 0
        negative_resolved_count = 0

        for row in rows:
            value_key = row.feedback_value or "unknown"
            status_key = row.feedback_status or "unknown"
            source_key = row.source or "unknown"
            date_key = row.feedback_created_at.strftime("%Y-%m-%d") if row.feedback_created_at else "unknown"

            by_value[value_key] = by_value.get(value_key, 0) + 1
            by_status[status_key] = by_status.get(status_key, 0) + 1
            by_source[source_key] = by_source.get(source_key, 0) + 1
            by_date[date_key] = by_date.get(date_key, 0) + 1
            if row.feedback_reason:
                by_reason[row.feedback_reason] = by_reason.get(row.feedback_reason, 0) + 1
            if row.feedback_status == "resolved":
                resolved_count += 1
                if row.feedback_value == "negative":
                    negative_resolved_count += 1
            if row.feedback_status == "open":
                open_count += 1

        total = len(rows)
        negative_count = by_value.get("negative", 0)
        positive_count = by_value.get("positive", 0)
        return {
            "days": days,
            "total_feedback": total,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "open_count": open_count,
            "resolved_count": resolved_count,
            "positive_rate": round(positive_count / total, 4) if total else 0,
            "resolution_rate": round(negative_resolved_count / negative_count, 4) if negative_count else 0,
            "by_value": by_value,
            "by_status": by_status,
            "by_reason": by_reason,
            "by_source": by_source,
            "by_date": by_date,
        }

    def export_feedback_eval_bundle(
        self,
        db: Session,
        *,
        user_id: int | None = None,
        include_all_users: bool = False,
        days: int = 30,
    ) -> dict:
        rows = self.list_feedback_records(
            db=db,
            user_id=user_id,
            include_all_users=include_all_users,
            days=days,
            limit=1000,
            feedback_value="negative",
            feedback_status=None,
            source=None,
        )
        if settings.EVAL_BUNDLE_OUTPUT_DIR:
            bundle_dir = Path(settings.EVAL_BUNDLE_OUTPUT_DIR)
        else:
            bundle_dir = Path("eval") / "bundles" / "feedback_autogen"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        dataset_path = bundle_dir / "qa_dataset.json"
        items = []
        for row in rows:
            citations = document_qa_service.serialize_record(row).get("citations") or []
            items.append(
                {
                    "qa_record_id": row.id,
                    "document_id": row.document_id,
                    "document_title": row.document.title if row.document else None,
                    "question": row.question,
                    "previous_answer": row.answer,
                    "feedback_reason": row.feedback_reason,
                    "feedback_note": row.feedback_note,
                    "citations": citations,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
            )
        dataset_path.write_text(
            json.dumps(
                {
                    "generated_at": utc_now().isoformat(),
                    "days": days,
                    "count": len(items),
                    "items": items,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "bundle_dir": str(bundle_dir),
            "dataset_path": str(dataset_path),
            "count": len(items),
        }

