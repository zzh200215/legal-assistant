import json
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.meeting import Meeting, MeetingSummary
from app.models.user import User
from app.services.analysis_service import analysis_service
from app.services.document_service import IMAGE_FILE_TYPES, UPLOAD_DIR, extract_file_text
from app.services.meeting_transcription_service import meeting_transcription_service
from app.services.storage_service import storage_service


class MeetingService:
    @staticmethod
    def _can_access_meeting(
        meeting: Meeting,
        *,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> bool:
        if role == "admin":
            return True
        if meeting.user_id == user_id:
            return True
        if department_id and meeting.department_id and department_id == meeting.department_id:
            return True
        if organization_id and meeting.organization_id and organization_id == meeting.organization_id:
            return True
        return False

    @staticmethod
    def _dump_json(value) -> str:
        return json.dumps(value or [], ensure_ascii=False)

    @staticmethod
    def _load_json(value: str | None, fallback):
        if not value:
            return fallback
        return json.loads(value)

    def serialize_summary(self, summary: MeetingSummary) -> dict:
        return {
            "meeting_id": summary.meeting_id,
            "theme": summary.theme or "",
            "summary": summary.summary or "",
            "topics": self._load_json(summary.topics, []),
            "decisions": self._load_json(summary.decisions, []),
            "action_items": self._load_json(summary.action_items, []),
            "risks": self._load_json(summary.risks, []),
        }

    @staticmethod
    def match_scope(
        meeting: Meeting,
        *,
        user_id: int,
        organization_id: int | None = None,
        department_id: int | None = None,
        scope: str | None = None,
    ) -> bool:
        normalized_scope = (scope or "all").strip().lower()
        is_mine = meeting.user_id == user_id
        is_same_department = bool(
            not is_mine
            and department_id
            and meeting.department_id
            and department_id == meeting.department_id
        )
        is_same_organization = bool(
            not is_mine
            and not is_same_department
            and organization_id
            and meeting.organization_id
            and organization_id == meeting.organization_id
        )

        if normalized_scope == "all":
            return True
        if normalized_scope == "mine":
            return is_mine
        if normalized_scope == "department":
            return is_same_department
        if normalized_scope == "organization":
            return is_same_organization
        if normalized_scope == "shared":
            return is_same_department or is_same_organization
        return True

    def create(self, title: str, transcript: str | None, user_id: int, db: Session) -> Meeting:
        owner = db.query(User).filter(User.id == user_id).first()
        meeting = Meeting(
            user_id=user_id,
            organization_id=owner.organization_id if owner else None,
            department_id=owner.department_id if owner else None,
            title=title,
            transcript=transcript,
            status="pending",
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting

    def create_from_uploaded_image(self, title: str | None, file, user_id: int, db: Session) -> Meeting:
        ext = Path(file.filename or "").suffix.lower()
        file_type = ext.lstrip(".") if ext else ""
        allowed_types = set(IMAGE_FILE_TYPES) | {"pdf", ".pdf"}
        if ext not in allowed_types and file_type not in allowed_types:
            raise ValueError("仅支持图片或 PDF 格式的会议纪要上传")

        unique_name = f"{uuid.uuid4().hex}{ext or '.png'}"
        file_path = storage_service.save_bytes(
            base_dir=UPLOAD_DIR,
            filename=unique_name,
            content=file.file.read(),
        )

        transcript = extract_file_text(str(file_path), file_type or ext)
        normalized_title = (title or "").strip() or Path(file.filename or unique_name).stem or "会议纪要图片"
        if not transcript:
            raise ValueError("未从图片或 PDF 中识别到会议内容")
        return self.create(
            title=normalized_title,
            transcript=transcript,
            user_id=user_id,
            db=db,
        )

    def create_from_uploaded_audio(
        self,
        *,
        title: str | None,
        file,
        transcript_text: str | None,
        user_id: int,
        db: Session,
    ) -> Meeting:
        ext = Path(file.filename or "").suffix.lower()
        allowed_types = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
        if ext not in allowed_types:
            raise ValueError("仅支持常见音频格式上传")

        unique_name = f"{uuid.uuid4().hex}{ext or '.mp3'}"
        file_path = storage_service.save_bytes(
            base_dir=UPLOAD_DIR,
            filename=unique_name,
            content=file.file.read(),
        )

        normalized_transcript = (transcript_text or "").strip()
        transcript_segments = []
        transcript_source = "manual"
        if not normalized_transcript:
            transcription = meeting_transcription_service.transcribe(file_path)
            normalized_transcript = transcription.text
            transcript_segments = transcription.segments
            transcript_source = "asr"

        owner = db.query(User).filter(User.id == user_id).first()

        meeting = Meeting(
            user_id=user_id,
            organization_id=owner.organization_id if owner else None,
            department_id=owner.department_id if owner else None,
            title=(title or "").strip() or Path(file.filename or unique_name).stem or "会议音频",
            transcript=normalized_transcript,
            transcript_segments=self._dump_json(transcript_segments) if transcript_segments else None,
            transcript_source=transcript_source,
            audio_path=str(file_path),
            status="pending",
        )
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting

    def get_transcript(self, meeting: Meeting) -> dict:
        return {
            "meeting_id": meeting.id,
            "source": meeting.transcript_source or "manual",
            "text": meeting.transcript or "",
            "segments": self._load_json(meeting.transcript_segments, []),
        }

    def get(
        self,
        meeting_id: int,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        require_owner: bool = False,
    ) -> Meeting | None:
        meeting = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not meeting:
            return None
        if user_id is None:
            return meeting
        if require_owner:
            return meeting if meeting.user_id == user_id else None
        return meeting if self._can_access_meeting(
            meeting,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        ) else None

    async def summarize(self, meeting_id: int, db: Session, user_id: int | None = None) -> MeetingSummary:
        meeting = self.get(meeting_id, db, user_id=user_id, require_owner=True)
        if not meeting:
            raise ValueError("Meeting not found")
        if not meeting.transcript:
            raise ValueError("Meeting transcript is empty")

        data = await analysis_service.summarize_meeting(meeting.transcript, user_id=user_id)

        summary = db.query(MeetingSummary).filter(MeetingSummary.meeting_id == meeting_id).first()
        if not summary:
            summary = MeetingSummary(meeting_id=meeting_id)
            db.add(summary)

        summary.theme = data.get("theme", "")
        summary.summary = data.get("summary", "")
        summary.topics = self._dump_json(data.get("topics", []))
        summary.decisions = self._dump_json(data.get("decisions", []))
        summary.action_items = self._dump_json(data.get("action_items", []))
        summary.risks = self._dump_json(data.get("risks", []))

        meeting.status = "summarized"
        db.commit()
        db.refresh(summary)
        return summary

    def get_summary(
        self,
        meeting_id: int,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> MeetingSummary | None:
        meeting = self.get(
            meeting_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not meeting:
            return None
        return db.query(MeetingSummary).filter(MeetingSummary.meeting_id == meeting_id).first()

    def get_action_items(
        self,
        meeting_id: int,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[dict]:
        summary = self.get_summary(
            meeting_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not summary or not summary.action_items:
            return []
        return self._load_json(summary.action_items, [])

    def extract_tasks(self, meeting_id: int, user_id: int, db: Session):
        from app.services.task_service import task_service

        action_items = self.get_action_items(meeting_id, db, user_id=user_id)
        if not action_items:
            raise ValueError("No action items found, please summarize first")
        return task_service.create_from_action_items(
            action_items=action_items,
            user_id=user_id,
            source_id=meeting_id,
            db=db,
            source_type="meeting",
        )

    async def extract_decisions(self, meeting_id: int, db: Session, user_id: int | None = None) -> list[dict]:
        meeting = self.get(meeting_id, db, user_id=user_id, require_owner=True)
        if not meeting:
            raise ValueError("Meeting not found")
        if not meeting.transcript:
            raise ValueError("Meeting transcript is empty")
        return await analysis_service.extract_meeting_decisions(meeting.transcript, user_id=user_id)

    async def extract_topics(self, meeting_id: int, db: Session, user_id: int | None = None) -> list[dict]:
        meeting = self.get(meeting_id, db, user_id=user_id, require_owner=True)
        if not meeting:
            raise ValueError("Meeting not found")
        if not meeting.transcript:
            raise ValueError("Meeting transcript is empty")
        return await analysis_service.extract_meeting_topics(meeting.transcript, user_id=user_id)


meeting_service = MeetingService()
