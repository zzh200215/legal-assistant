import json

from sqlalchemy.orm import Session

from app.models.email import EmailDraft
from app.models.user import User
from app.services.email_ai_service import email_ai_service


class EmailService:
    @staticmethod
    def _can_access_draft(
        draft: EmailDraft,
        *,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> bool:
        if role == "admin":
            return True
        if draft.user_id == user_id:
            return True
        if department_id and draft.department_id and department_id == draft.department_id:
            return True
        if organization_id and draft.organization_id and organization_id == draft.organization_id:
            return True
        return False

    @staticmethod
    def _dump_key_points(value: list[str] | None) -> str | None:
        if not value:
            return None
        return json.dumps([item for item in value if item], ensure_ascii=False)

    @staticmethod
    def _load_key_points(value: str | None) -> list[str]:
        if not value:
            return []
        return json.loads(value)

    def serialize_draft(self, draft: EmailDraft) -> dict:
        return {
            "id": draft.id,
            "user_id": draft.user_id,
            "organization_id": draft.organization_id,
            "department_id": draft.department_id,
            "subject": draft.subject,
            "recipient": draft.recipient,
            "cc": draft.cc,
            "content": draft.content,
            "purpose": draft.purpose,
            "key_points": self._load_key_points(draft.key_points),
            "need_action": draft.need_action,
            "generation_type": draft.generation_type,
            "original_email": draft.original_email,
            "reply_goal": draft.reply_goal,
            "tone": draft.tone,
            "status": draft.status,
            "metadata_json": draft.metadata_json,
            "created_at": draft.created_at,
            "updated_at": draft.updated_at,
        }

    async def generate(
        self,
        purpose: str,
        key_points: list[str] | None = None,
        tone: str = "professional",
        recipient: str | None = None,
        need_action: bool = False,
        generation_type: str = "generate",
        metadata: dict | None = None,
        user_id: int = 1,
        db: Session | None = None,
    ) -> dict:
        normalized_points = [item.strip() for item in (key_points or []) if item and item.strip()]
        owner = db.query(User).filter(User.id == user_id).first() if db else None
        subjects, content = await email_ai_service.generate_email(
            recipient=recipient,
            purpose=purpose,
            key_points=normalized_points,
            tone=tone,
            need_action=need_action,
            user_id=user_id,
        )

        draft = EmailDraft(
            user_id=user_id,
            organization_id=owner.organization_id if owner else None,
            department_id=owner.department_id if owner else None,
            subject=subjects[0] if subjects else purpose,
            recipient=recipient,
            content=content,
            purpose=purpose,
            key_points=self._dump_key_points(normalized_points),
            need_action=need_action,
            generation_type=generation_type,
            tone=tone,
            status="draft",
            metadata_json=json.dumps(metadata, ensure_ascii=False) if metadata else None,
        )
        if db:
            db.add(draft)
            db.commit()
            db.refresh(draft)

        return {"draft": draft, "subject_candidates": subjects}

    async def reply(
        self,
        original_email: str,
        reply_goal: str,
        tone: str = "professional",
        recipient: str | None = None,
        user_id: int = 1,
        db: Session | None = None,
    ) -> dict:
        owner = db.query(User).filter(User.id == user_id).first() if db else None
        subjects, content = await email_ai_service.reply_email(
            original_email=original_email,
            reply_goal=reply_goal,
            tone=tone,
            user_id=user_id,
        )

        draft = EmailDraft(
            user_id=user_id,
            organization_id=owner.organization_id if owner else None,
            department_id=owner.department_id if owner else None,
            subject=subjects[0] if subjects else "回复",
            recipient=recipient,
            content=content,
            purpose="回复来信",
            key_points=self._dump_key_points([reply_goal]),
            need_action=True,
            generation_type="reply",
            original_email=original_email,
            reply_goal=reply_goal,
            tone=tone,
            status="draft",
        )
        if db:
            db.add(draft)
            db.commit()
            db.refresh(draft)

        return {"draft": draft, "subject_candidates": subjects}

    async def switch_tone(
        self,
        draft_id: int,
        target_tone: str,
        db: Session,
        user_id: int | None = None,
    ) -> dict:
        draft = self.get(draft_id, db, user_id=user_id, require_owner=True)
        if not draft:
            raise ValueError("Draft not found")

        subjects, content = await email_ai_service.switch_tone(
            subject=draft.subject,
            content=draft.content,
            target_tone=target_tone,
            user_id=user_id,
        )

        new_draft = EmailDraft(
            user_id=draft.user_id,
            organization_id=draft.organization_id,
            department_id=draft.department_id,
            subject=subjects[0] if subjects else draft.subject,
            recipient=draft.recipient,
            cc=draft.cc,
            content=content,
            purpose=draft.purpose,
            key_points=draft.key_points,
            need_action=draft.need_action,
            generation_type="tone_switch",
            original_email=draft.original_email,
            reply_goal=draft.reply_goal,
            tone=target_tone,
            status="draft",
        )
        db.add(new_draft)
        db.commit()
        db.refresh(new_draft)

        return {"draft": new_draft, "subject_candidates": subjects}

    async def summarize_thread(
        self,
        emails: list[str],
        user_id: int | None = None,
        db: Session | None = None,
    ) -> dict:
        return await email_ai_service.summarize_thread(emails, user_id=user_id)

    async def reply_from_thread(
        self,
        emails: list[str],
        reply_goal: str,
        *,
        tone: str = "professional",
        recipient: str | None = None,
        user_id: int = 1,
        db: Session | None = None,
    ) -> dict:
        summary = await self.summarize_thread(emails, user_id=user_id, db=db)
        summary_text = "\n".join(
            [
                f"摘要：{summary.get('summary') or ''}",
                "关键信息：" + "；".join(summary.get("key_points") or []),
                "待处理：" + "；".join(summary.get("pending_items") or []),
                f"建议下一步：{summary.get('next_action') or ''}",
            ]
        ).strip()
        original_email = "\n\n--- thread summary ---\n" + summary_text
        result = await self.reply(
            original_email=original_email,
            reply_goal=reply_goal,
            tone=tone,
            recipient=recipient,
            user_id=user_id,
            db=db,
        )
        result["thread_summary"] = summary
        return result

    async def polish(
        self,
        draft_id: int,
        instruction: str = "优化措辞，使其更专业",
        db: Session | None = None,
        user_id: int | None = None,
    ) -> EmailDraft:
        draft = self.get(draft_id, db, user_id=user_id, require_owner=True)
        if not draft:
            raise ValueError("Draft not found")

        subject, content = await email_ai_service.polish_email(
            subject=draft.subject,
            content=draft.content,
            instruction=instruction,
            user_id=user_id,
        )
        draft.subject = subject
        draft.content = content
        draft.status = "polished"

        if db:
            db.commit()
            db.refresh(draft)
        return draft

    def get(
        self,
        draft_id: int,
        db: Session,
        *,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        require_owner: bool = False,
    ) -> EmailDraft | None:
        draft = db.query(EmailDraft).filter(EmailDraft.id == draft_id).first()
        if not draft:
            return None
        if user_id is None:
            return draft
        if require_owner:
            return draft if draft.user_id == user_id else None
        return draft if self._can_access_draft(
            draft,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        ) else None

    def list_visible(
        self,
        *,
        db: Session,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        scope: str | None = None,
    ) -> list[EmailDraft]:
        rows = db.query(EmailDraft).order_by(EmailDraft.created_at.desc()).all()
        return [
            row
            for row in rows
            if self._can_access_draft(
                row,
                user_id=user_id,
                role=role,
                organization_id=organization_id,
                department_id=department_id,
            ) and self._match_scope(
                row.user_id,
                row.organization_id,
                row.department_id,
                user_id=user_id,
                organization_id=organization_id,
                department_id=department_id,
                scope=scope,
            )
        ]

    @staticmethod
    def _match_scope(
        owner_user_id: int | None,
        owner_organization_id: int | None,
        owner_department_id: int | None,
        *,
        user_id: int,
        organization_id: int | None = None,
        department_id: int | None = None,
        scope: str | None = None,
    ) -> bool:
        normalized_scope = (scope or "all").strip().lower()
        is_mine = owner_user_id == user_id
        is_same_department = bool(
            not is_mine
            and department_id
            and owner_department_id
            and department_id == owner_department_id
        )
        is_same_organization = bool(
            not is_mine
            and not is_same_department
            and organization_id
            and owner_organization_id
            and organization_id == owner_organization_id
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


email_service = EmailService()
