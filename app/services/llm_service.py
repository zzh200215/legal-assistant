import json

from app.core.llm_client import llm_client


class LLMService:
    async def chat(
        self,
        messages: list,
        stream: bool = False,
        temperature: float | None = None,
        action: str = "chat",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        trace_id: str | None = None,
        cacheable: bool = False,
        permission_fingerprint: str | None = None,
    ):
        return await llm_client.chat(
            messages,
            stream=stream,
            temperature=temperature,
            action=action,
            user_id=user_id,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
            trace_id=trace_id,
            cacheable=cacheable,
            permission_fingerprint=permission_fingerprint,
        )

    async def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        action: str = "generate",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        trace_id: str | None = None,
        cacheable: bool = False,
        permission_fingerprint: str | None = None,
    ) -> str:
        return await llm_client.generate(
            prompt,
            temperature=temperature,
            action=action,
            user_id=user_id,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
            trace_id=trace_id,
            cacheable=cacheable,
            permission_fingerprint=permission_fingerprint,
        )

    async def structured_generate(
        self,
        prompt: str,
        *,
        schema,
        temperature: float | None = None,
        action: str = "generate",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        trace_id: str | None = None,
        cacheable: bool = False,
        permission_fingerprint: str | None = None,
    ):
        return await llm_client.structured_generate(
            prompt,
            schema=schema,
            temperature=temperature,
            action=action,
            user_id=user_id,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
            trace_id=trace_id,
            cacheable=cacheable,
            permission_fingerprint=permission_fingerprint,
        )

    async def generate_with_images(
        self,
        prompt: str,
        image_urls: list[str],
        temperature: float | None = None,
        action: str = "generate_with_images",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        trace_id: str | None = None,
    ) -> str:
        return await llm_client.generate_with_images(
            prompt,
            image_urls=image_urls,
            temperature=temperature,
            action=action,
            user_id=user_id,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
            trace_id=trace_id,
        )

    def _strip_code_fence(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    def parse_json_array(self, text: str) -> list[dict]:
        text = self._strip_code_fence(text)
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                result = [result]
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            return []

    def parse_json_object(self, text: str) -> dict:
        text = self._strip_code_fence(text)
        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _parse_json_array(self, text: str) -> list[dict]:
        return self.parse_json_array(text)

    def _parse_json_object(self, text: str) -> dict:
        return self.parse_json_object(text)


llm_service = LLMService()
