import asyncio
import json
import time
import uuid
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.llm_provider_adapter import provider_adapter
from app.core.observability_sanitizer import sanitize_observability_error_message, sanitize_observability_excerpt
from app.services.llm_governance_service import llm_governance_service

settings = get_settings()
OPENAI_COMPATIBLE_EMBED_BATCH_SIZE = 10
DEFAULT_REQUEST_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.5

_COMPLEX_REQUEST_MARKERS = (
    "合同", "法律", "仲裁", "诉讼", "风险", "对比", "比较", "差异", "条款", "流程",
    "分析", "总结", "方案", "证据", "责任", "条件", "金额", "日期", "分别", "以及",
)
_PRIMARY_ACTION_PREFIXES = ("legal_", "agent_", "agentic_", "rag_", "text_to_sql")
_PRIMARY_ACTIONS = {"generate_with_images", "embedding"}


@dataclass(frozen=True)
class _ModelTarget:
    role: str
    model: str
    provider: str
    base_url: str
    api_key: str

ACTION_PROMPT_TEMPLATE_MAP = {
    "document_summary": "document_summary",
    "document_risk_extract": "document_risk_extract",
    "document_todo_extract": "document_todo_extract",
    "document_clause_extract": "document_clause_extract",
    "document_compare": "document_compare",
    "meeting_summary": "meeting_summary",
    "meeting_decision_extract": "meeting_decision_extract",
    "meeting_topic_extract": "meeting_topic_extract",
    "email_generate": "email_generate",
    "email_reply": "email_reply",
    "email_tone_switch": "email_tone_switch",
    "email_thread_summary": "email_thread_summary",
    "email_polish": "email_polish",
    "task_extract_from_chat": "task_extract_from_chat",
    "task_decompose": "task_decompose",
    "rag_answer": "rag_answer",
    "legal_consultation": "legal_consultation",
    "legal_contract_review": "legal_contract_review",
    "legal_draft_generation": "legal_draft_generation",
    "legal_followup": "legal_followup",
    "legal_contract_compare": "legal_contract_compare",
    "agent_plan": "agent_system_prompt",
    "agent_plan_preview": "agent_plan_preview",
    "embedding": None,
}


class LLMClient:
    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.base_url = self._resolve_base_url(provider=self.provider).rstrip("/")
        self.api_key = settings.LLM_API_KEY
        self.llm_model = settings.LLM_MODEL
        self.vision_model = settings.LLM_VISION_MODEL or settings.LLM_MODEL
        self.embedding_model = settings.EMBEDDING_MODEL
        self.primary_target = _ModelTarget(
            role="primary",
            model=self.llm_model,
            provider=self.provider,
            base_url=self.base_url,
            api_key=self.api_key,
        )
        self.small_target = self._build_small_target()

    def _resolve_base_url(self, *, provider: str, configured_url: str = "") -> str:
        if configured_url:
            return configured_url
        if provider == "ollama":
            return settings.OLLAMA_BASE_URL
        return settings.LLM_API_BASE_URL or settings.OLLAMA_BASE_URL

    def _build_small_target(self) -> _ModelTarget | None:
        model = str(settings.LLM_SMALL_MODEL or "").strip()
        if not settings.LLM_MODEL_ROUTING_ENABLED or not model:
            return None
        provider = str(settings.LLM_SMALL_MODEL_PROVIDER or "openai_compatible").strip()
        base_url = self._resolve_base_url(
            provider=provider,
            configured_url=str(settings.LLM_SMALL_MODEL_API_BASE_URL or "").strip(),
        ).rstrip("/")
        api_key = str(settings.LLM_SMALL_MODEL_API_KEY or "").strip() or self.api_key
        return _ModelTarget("small", model, provider, base_url, api_key)

    def _build_headers(self, target: _ModelTarget | None = None) -> dict[str, str]:
        target = target or self.primary_target
        return provider_adapter(target.provider).headers(target.api_key)

    def _chat_url(self, target: _ModelTarget | None = None) -> str:
        target = target or self.primary_target
        return provider_adapter(target.provider).chat_url(target.base_url)

    def _generate_url(self, target: _ModelTarget | None = None) -> str:
        target = target or self.primary_target
        return provider_adapter(target.provider).generate_url(target.base_url)

    def _embedding_url(self) -> str:
        return provider_adapter(self.provider).embedding_url(self.base_url)

    def _build_chat_payload(self, messages: list[dict], stream: bool, temperature: float, target: _ModelTarget | None = None) -> dict:
        target = target or self.primary_target
        return provider_adapter(target.provider).chat_payload(
            target.model, self._normalize_messages(messages), stream, temperature
        )

    def _build_generate_payload(self, prompt: str, temperature: float, target: _ModelTarget | None = None) -> dict:
        target = target or self.primary_target
        return provider_adapter(target.provider).generate_payload(target.model, prompt, temperature)

    def _build_multimodal_generate_payload(
        self,
        *,
        prompt: str,
        image_urls: list[str],
        temperature: float,
    ) -> dict:
        content = [{"type": "text", "text": prompt}]
        for image_url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": image_url}})
        return {
            "model": self.vision_model,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
            "temperature": temperature,
        }

    def _build_embed_payload(self, texts: list[str]) -> dict:
        return provider_adapter(self.provider).embedding_payload(self.embedding_model, texts)

    @staticmethod
    def _normalize_messages(messages: list[dict]) -> list[dict]:
        normalized = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            content = message.get("content") or ""
            if isinstance(content, list):
                parts = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type") or "")
                    if item_type == "text":
                        parts.append({"type": "text", "text": str(item.get("text") or "")})
                    elif item_type == "image_url":
                        image_url = item.get("image_url")
                        if isinstance(image_url, dict):
                            url = str(image_url.get("url") or "")
                        else:
                            url = str(image_url or "")
                        if url:
                            parts.append({"type": "image_url", "image_url": {"url": url}})
                normalized.append({"role": role, "content": parts})
            else:
                normalized.append({"role": role, "content": str(content)})
        return normalized

    async def _post_json_with_retry(
        self,
        client: httpx.AsyncClient,
        *,
        url: str,
        payload: dict,
        headers: dict[str, str],
        retries: int = DEFAULT_REQUEST_RETRIES,
    ) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ProxyError) as exc:
                last_error = exc
                if attempt == retries:
                    break
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
            except httpx.HTTPStatusError as exc:
                # 参数、鉴权和内容审核等 4xx 不应换模型重试；5xx 视为服务端不可用。
                if exc.response is None or exc.response.status_code < 500:
                    raise
                last_error = exc
                if attempt == retries:
                    break
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Request failed without a captured exception")

    def _extract_usage(self, data: dict, provider: str | None = None) -> tuple[int, int]:
        return provider_adapter(provider or self.provider).extract_usage(data)

    def _extract_chat_content(self, data: dict, provider: str | None = None) -> str:
        return provider_adapter(provider or self.provider).extract_chat_content(data)

    def _extract_generate_content(self, data: dict, provider: str | None = None) -> str:
        if (provider or self.provider) == "ollama":
            return data.get("response", "")
        return self._extract_chat_content(data, provider=provider)

    @staticmethod
    def _chunk_texts(texts: list[str], batch_size: int) -> list[list[str]]:
        return [texts[index : index + batch_size] for index in range(0, len(texts), batch_size)]

    def _record_usage(
        self,
        data: dict,
        model: str,
        action: str,
        duration_ms: int,
        user_id: int | None = None,
        request_excerpt: str | None = None,
        response_excerpt: str | None = None,
        error_message: str | None = None,
        status: str = "success",
        prompt_template: str | None = None,
        prompt_version: int | None = None,
        provider: str | None = None,
        request_id: str | None = None,
        routing_role: str | None = None,
        routing_stage: str | None = None,
    ):
        try:
            from app.core.database import SessionLocal
            from app.models.llm_call_log import LLMCallLog
            from app.services.prompt_service import prompt_service
            from app.services.token_service import token_service

            prompt_tokens, completion_tokens = self._extract_usage(data, provider=provider)
            if prompt_template is None:
                mapped_template = ACTION_PROMPT_TEMPLATE_MAP.get(action)
                if mapped_template:
                    metadata = prompt_service.get_template_metadata(mapped_template, user_id=user_id)
                    prompt_template = metadata.get("prompt_template")
                    prompt_version = metadata.get("prompt_version")
            module_name = self._infer_module_name(action)

            db = SessionLocal()
            try:
                token_service.record(
                    model=model,
                    db=db,
                    user_id=user_id,
                    action=action,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    duration_ms=duration_ms,
                )
                db.add(
                    LLMCallLog(
                        user_id=user_id,
                        module_name=module_name,
                        action=action,
                        model_name=model,
                        prompt_template=prompt_template,
                        prompt_version=prompt_version,
                        input_tokens=prompt_tokens,
                        output_tokens=completion_tokens,
                        duration_ms=duration_ms,
                        status=status,
                        request_id=request_id,
                        routing_role=routing_role,
                        routing_stage=routing_stage,
                        error_message=sanitize_observability_error_message(action, error_message),
                        request_excerpt=sanitize_observability_excerpt(action, request_excerpt, kind="request"),
                        response_excerpt=sanitize_observability_excerpt(action, response_excerpt, kind="response"),
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            pass

    def _sanitize_observability_excerpt(self, action: str, excerpt: str | None, *, kind: str) -> str | None:
        return sanitize_observability_excerpt(action, excerpt, kind=kind)

    @staticmethod
    def _infer_module_name(action: str) -> str:
        if action == "embedding":
            return "document"
        if action.startswith("document_") or action.startswith("rag_"):
            return "document"
        if action.startswith("meeting_"):
            return "meeting"
        if action.startswith("email_"):
            return "email"
        if action.startswith("task_"):
            return "task"
        if action.startswith("agent_"):
            return "agent"
        if action.startswith("chat"):
            return "chat"
        if action.startswith("legal_"):
            return "legal"
        return "system"

    @staticmethod
    def _messages_to_text(messages: list[dict]) -> str:
        return "\n".join(str(item.get("content") or "") for item in messages if isinstance(item, dict))

    def _select_text_target(self, text: str, action: str) -> _ModelTarget:
        """Route short, low-risk text requests to the configured small model."""
        if not self.small_target:
            return self.primary_target
        normalized_action = (action or "").lower()
        normalized_text = text or ""
        if (
            normalized_action in _PRIMARY_ACTIONS
            or normalized_action.startswith(_PRIMARY_ACTION_PREFIXES)
            or len(normalized_text) > settings.LLM_SIMPLE_REQUEST_MAX_CHARS
            or any(marker in normalized_text for marker in _COMPLEX_REQUEST_MARKERS)
        ):
            return self.primary_target
        return self.small_target

    @staticmethod
    def _same_target(left: _ModelTarget, right: _ModelTarget) -> bool:
        return (left.model, left.provider, left.base_url, left.api_key) == (right.model, right.provider, right.base_url, right.api_key)

    def _candidate_targets(self, text: str, action: str) -> list[_ModelTarget]:
        primary_choice = self._select_text_target(text, action)
        targets = [primary_choice]
        if not settings.LLM_MODEL_FALLBACK_ENABLED or not self.small_target:
            return targets
        alternate = self.small_target if primary_choice.role == "primary" else self.primary_target
        if primary_choice.role == "small" and not settings.LLM_SMALL_MODEL_FALLBACK_TO_PRIMARY:
            return targets
        if not self._same_target(primary_choice, alternate):
            targets.append(alternate)
        return targets

    @staticmethod
    def _is_provider_failure(exc: Exception) -> bool:
        if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ProxyError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return bool(exc.response and exc.response.status_code >= 500)
        return False

    def _record_target_usage(
        self,
        data: dict,
        target: _ModelTarget,
        action: str,
        duration_ms: int,
        user_id: int | None,
        **kwargs,
    ) -> None:
        # 仅在备用目标的协议不同于主目标时显式传入 provider，保持既有测试桩兼容。
        if target.provider != self.provider:
            kwargs["provider"] = target.provider
        self._record_usage(data, target.model, action, duration_ms, user_id, **kwargs)

    async def _request_text_once(
        self,
        *,
        target: _ModelTarget,
        request_type: str,
        messages: list[dict] | None = None,
        prompt: str | None = None,
        temperature: float,
        action: str,
        user_id: int | None,
        prompt_template: str | None,
        prompt_version: int | None,
        request_id: str | None = None,
        routing_stage: str | None = None,
    ) -> str:
        is_chat = request_type == "chat"
        request_excerpt = json.dumps(messages, ensure_ascii=False) if is_chat else str(prompt or "")
        url = self._chat_url(target) if is_chat else self._generate_url(target)
        payload = (
            self._build_chat_payload(messages or [], stream=False, temperature=temperature, target=target)
            if is_chat
            else self._build_generate_payload(prompt or "", temperature=temperature, target=target)
        )
        start = time.time()
        retries = settings.LLM_PRIMARY_REQUEST_RETRIES if target.role == "primary" else settings.LLM_FALLBACK_REQUEST_RETRIES
        try:
            async with httpx.AsyncClient(timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS) as client:
                data = await self._post_json_with_retry(
                    client, url=url, payload=payload, headers=self._build_headers(target), retries=retries,
                )
        except Exception as exc:
            self._record_target_usage(
                {}, target, action, int((time.time() - start) * 1000), user_id,
                request_excerpt=request_excerpt, error_message=str(exc), status="error",
                prompt_template=prompt_template, prompt_version=prompt_version,
                request_id=request_id, routing_role=target.role, routing_stage=routing_stage,
            )
            raise
        response_excerpt = (
            self._extract_chat_content(data, provider=target.provider)
            if is_chat else self._extract_generate_content(data, provider=target.provider)
        )
        self._record_target_usage(
            data, target, action, int((time.time() - start) * 1000), user_id,
            request_excerpt=request_excerpt, response_excerpt=response_excerpt,
            prompt_template=prompt_template, prompt_version=prompt_version,
            request_id=request_id, routing_role=target.role, routing_stage=routing_stage,
        )
        return response_excerpt

    async def _request_text_with_routing(self, *, source_text: str, **kwargs) -> str:
        targets = self._candidate_targets(source_text, str(kwargs.get("action") or ""))
        request_id = str(uuid.uuid4())
        last_error: Exception | None = None
        for index, target in enumerate(targets):
            try:
                return await self._request_text_once(
                    target=target,
                    request_id=request_id,
                    routing_stage="initial" if index == 0 else "fallback",
                    **kwargs,
                )
            except Exception as exc:
                last_error = exc
                if index == len(targets) - 1 or not self._is_provider_failure(exc):
                    raise
        raise last_error or RuntimeError("No LLM target is available")

    async def chat(
        self,
        messages: list[dict],
        stream: bool = False,
        temperature: float = 0.7,
        action: str = "chat",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
    ) -> str:
        llm_governance_service.enforce_chat_request(messages=messages, user_id=user_id, action=action)
        return await self._request_text_with_routing(
            source_text=self._messages_to_text(messages),
            request_type="chat",
            messages=messages,
            temperature=temperature,
            action=action,
            user_id=user_id,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
        )

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        action: str = "generate",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
    ) -> str:
        llm_governance_service.enforce_generate_request(prompt=prompt, user_id=user_id, action=action)
        return await self._request_text_with_routing(
            source_text=prompt,
            request_type="generate",
            prompt=prompt,
            temperature=temperature,
            action=action,
            user_id=user_id,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
        )

    async def generate_with_images(
        self,
        prompt: str,
        image_urls: list[str],
        temperature: float = 0.7,
        action: str = "generate_with_images",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
    ) -> str:
        llm_governance_service.enforce_generate_request(prompt=prompt, user_id=user_id, action=action)
        url = self._generate_url()
        payload = self._build_multimodal_generate_payload(
            prompt=prompt,
            image_urls=image_urls,
            temperature=temperature,
        )
        headers = self._build_headers()
        start = time.time()
        request_excerpt = json.dumps(
            {"prompt": prompt[:500], "image_count": len(image_urls)},
            ensure_ascii=False,
        )
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                data = await self._post_json_with_retry(client, url=url, payload=payload, headers=headers)
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            self._record_usage(
                {},
                self.vision_model,
                action,
                duration_ms,
                user_id,
                request_excerpt=request_excerpt,
                error_message=str(exc),
                status="error",
                prompt_template=prompt_template,
                prompt_version=prompt_version,
            )
            raise
        duration_ms = int((time.time() - start) * 1000)
        response_excerpt = self._extract_generate_content(data)
        self._record_usage(
            data,
            self.vision_model,
            action,
            duration_ms,
            user_id,
            request_excerpt=request_excerpt,
            response_excerpt=response_excerpt,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
        )
        return response_excerpt

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        action: str = "chat_stream",
        user_id: int | None = None,
        prompt_template: str | None = None,
        prompt_version: int | None = None,
    ):
        llm_governance_service.enforce_chat_request(messages=messages, user_id=user_id, action=action)
        request_excerpt = json.dumps(messages, ensure_ascii=False)
        targets = self._candidate_targets(self._messages_to_text(messages), action)
        request_id = str(uuid.uuid4())

        async def stream_from_target(target: _ModelTarget, routing_stage: str):
            start = time.time()
            full_response = ""
            last_data: dict = {}
            retries = settings.LLM_PRIMARY_REQUEST_RETRIES if target.role == "primary" else settings.LLM_FALLBACK_REQUEST_RETRIES
            try:
                async with httpx.AsyncClient(timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS) as client:
                    # Stream connection failures use the same bounded retry policy as normal requests.
                    for attempt in range(1, retries + 1):
                        try:
                            async with client.stream(
                                "POST",
                                self._chat_url(target),
                                json=self._build_chat_payload(messages, stream=True, temperature=temperature, target=target),
                                headers=self._build_headers(target),
                            ) as resp:
                                resp.raise_for_status()
                                async for line in resp.aiter_lines():
                                    if not line.strip():
                                        continue
                                    adapter = provider_adapter(target.provider)
                                    if target.provider == "ollama":
                                        payload_text = line
                                    else:
                                        if not line.startswith("data:"):
                                            continue
                                        payload_text = line[5:].strip()
                                        if payload_text == "[DONE]":
                                            break
                                    last_data = json.loads(payload_text)
                                    content, done = adapter.extract_stream_chunk(last_data)
                                    if content:
                                        full_response += content
                                        yield content
                                    if done:
                                        break
                                self._record_target_usage(
                                    last_data, target, action, int((time.time() - start) * 1000), user_id,
                                    request_excerpt=request_excerpt, response_excerpt=full_response,
                                    prompt_template=prompt_template, prompt_version=prompt_version,
                                    request_id=request_id, routing_role=target.role, routing_stage=routing_stage,
                                )
                                return
                        except httpx.HTTPStatusError as exc:
                            if exc.response is None or exc.response.status_code < 500 or attempt == retries:
                                raise
                            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ProxyError):
                            if attempt == retries:
                                raise
                            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
            except Exception as exc:
                self._record_target_usage(
                    {}, target, action, int((time.time() - start) * 1000), user_id,
                    request_excerpt=request_excerpt, response_excerpt=full_response, error_message=str(exc), status="error",
                    prompt_template=prompt_template, prompt_version=prompt_version,
                    request_id=request_id, routing_role=target.role, routing_stage=routing_stage,
                )
                raise

        for index, target in enumerate(targets):
            emitted = False
            try:
                async for chunk in stream_from_target(target, "initial" if index == 0 else "fallback"):
                    emitted = True
                    yield chunk
                return
            except Exception as exc:
                # 已输出内容后不能无缝切换模型，否则会造成重复或前后文不一致。
                if emitted or index == len(targets) - 1 or not self._is_provider_failure(exc):
                    raise

    async def embed(
        self,
        texts: list[str],
        *,
        user_id: int | None = None,
        action: str = "embedding",
    ) -> list[list[float]]:
        if not texts:
            return []
        llm_governance_service.enforce_embedding_request(texts=texts, user_id=user_id, action=action)

        url = self._embedding_url()
        headers = self._build_headers()
        batches = [texts]
        adapter = provider_adapter(self.provider)
        if not adapter.uses_native_embedding_batch:
            batches = self._chunk_texts(texts, OPENAI_COMPATIBLE_EMBED_BATCH_SIZE)

        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for batch in batches:
                payload = self._build_embed_payload(batch)
                start = time.time()
                request_excerpt = json.dumps({"input_count": len(batch), "sample": batch[:2]}, ensure_ascii=False)
                try:
                    data = await self._post_json_with_retry(client, url=url, payload=payload, headers=headers)
                except Exception as exc:
                    duration_ms = int((time.time() - start) * 1000)
                    self._record_usage(
                        {},
                        self.embedding_model,
                        "embedding",
                        duration_ms,
                        request_excerpt=request_excerpt,
                        error_message=str(exc),
                        status="error",
                    )
                    raise

                duration_ms = int((time.time() - start) * 1000)
                response_excerpt = f"embedding_count={len(batch)}"
                self._record_usage(
                    data,
                    self.embedding_model,
                    "embedding",
                    duration_ms,
                    request_excerpt=request_excerpt,
                    response_excerpt=response_excerpt,
                )
                embeddings.extend(adapter.extract_embeddings(data))
        return embeddings


llm_client = LLMClient()
