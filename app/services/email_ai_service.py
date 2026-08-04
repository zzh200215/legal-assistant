from app.services.llm_service import llm_service
from app.services.prompt_service import prompt_service


TONE_MAP = {
    "professional": "正式商务",
    "casual": "轻松随意",
    "friendly": "友好亲切",
    "assertive": "坚定直接",
}


class EmailAIService:
    async def generate_email(
        self,
        recipient: str | None,
        purpose: str,
        key_points: list[str] | None = None,
        tone: str = "professional",
        need_action: bool = False,
        user_id: int | None = None,
    ) -> tuple[list[str], str]:
        prompt = prompt_service.render_by_name(
            "email_generate",
            user_id=user_id,
            recipient=recipient or "未指定",
            purpose=purpose,
            key_points="\n".join([f"- {item}" for item in (key_points or [])]) or "- 无额外补充",
            tone=TONE_MAP.get(tone, "正式商务"),
            need_action="是" if need_action else "否",
        )
        raw = await llm_service.generate(
            prompt,
            temperature=0.7,
            action="email_generate",
            user_id=user_id,
        )
        return self._parse_subjects_and_content(raw, purpose)

    async def reply_email(
        self,
        original_email: str,
        reply_goal: str,
        tone: str = "professional",
        user_id: int | None = None,
    ) -> tuple[list[str], str]:
        prompt = prompt_service.render_by_name(
            "email_reply",
            user_id=user_id,
            original_email=original_email[:4000],
            reply_goal=reply_goal,
            tone=TONE_MAP.get(tone, "正式商务"),
        )
        raw = await llm_service.generate(
            prompt,
            temperature=0.7,
            action="email_reply",
            user_id=user_id,
        )
        return self._parse_subjects_and_content(raw, "回复")

    async def switch_tone(
        self,
        subject: str,
        content: str,
        target_tone: str,
        user_id: int | None = None,
    ) -> tuple[list[str], str]:
        prompt = prompt_service.render_by_name(
            "email_tone_switch",
            user_id=user_id,
            subject=subject,
            content=content,
            target_tone=TONE_MAP.get(target_tone, "正式商务"),
        )
        raw = await llm_service.generate(
            prompt,
            temperature=0.7,
            action="email_tone_switch",
            user_id=user_id,
        )
        return self._parse_subjects_and_content(raw, subject)

    async def summarize_thread(self, emails: list[str], user_id: int | None = None) -> dict:
        combined = "\n\n---\n\n".join([f"邮件{i + 1}:\n{email}" for i, email in enumerate(emails)])
        prompt = prompt_service.render_by_name(
            "email_thread_summary",
            user_id=user_id,
            thread_content=combined[:8000],
        )
        raw = await llm_service.generate(
            prompt,
            temperature=0.3,
            action="email_thread_summary",
            user_id=user_id,
        )
        result = llm_service.parse_json_object(raw)
        if result:
            return result
        return {
            "summary": raw,
            "key_points": [],
            "pending_items": [],
            "participants": [],
            "next_action": "",
        }

    async def polish_email(
        self,
        subject: str,
        content: str,
        instruction: str,
        user_id: int | None = None,
    ) -> tuple[str, str]:
        prompt = prompt_service.render_by_name(
            "email_polish",
            user_id=user_id,
            instruction=instruction,
            subject=subject,
            content=content,
        )
        raw = await llm_service.generate(
            prompt,
            temperature=0.5,
            action="email_polish",
            user_id=user_id,
        )
        return self._parse_polished_email(raw, subject)

    def _parse_polished_email(self, raw: str, fallback_subject: str) -> tuple[str, str]:
        lines = raw.strip().split("\n")
        if lines and (lines[0].startswith("标题:") or lines[0].startswith("标题：")):
            subject = lines[0].split("：", 1)[-1].split(":", 1)[-1].strip() or fallback_subject
            content = "\n".join(lines[1:]).strip()
            return subject, content
        return fallback_subject, raw

    def _parse_subjects_and_content(self, raw: str, fallback_subject: str) -> tuple[list[str], str]:
        lines = raw.strip().split("\n")
        subjects = []
        content_lines = []
        in_content = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("标题1:") or stripped.startswith("标题1："):
                subjects.append(stripped.split("：", 1)[-1].split(":", 1)[-1].strip())
            elif stripped.startswith("标题2:") or stripped.startswith("标题2："):
                subjects.append(stripped.split("：", 1)[-1].split(":", 1)[-1].strip())
            elif stripped.startswith("标题3:") or stripped.startswith("标题3："):
                subjects.append(stripped.split("：", 1)[-1].split(":", 1)[-1].strip())
            elif stripped.startswith("正文:") or stripped.startswith("正文："):
                in_content = True
                rest = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
                if rest:
                    content_lines.append(rest)
            elif in_content:
                content_lines.append(line)

        content = "\n".join(content_lines).strip() if content_lines else raw
        if not subjects:
            subjects = [fallback_subject]
        return subjects, content


email_ai_service = EmailAIService()
