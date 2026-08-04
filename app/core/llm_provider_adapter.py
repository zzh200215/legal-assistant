"""Protocol adapters for model-provider wire formats.

Routing, retries, governance, and observability remain in ``LLMClient``.  The
adapters only translate that module's provider-neutral requests and responses
to the OpenAI-compatible and Ollama protocols.
"""

from __future__ import annotations

from typing import Protocol


class LLMProviderAdapter(Protocol):
    uses_native_embedding_batch: bool

    def headers(self, api_key: str) -> dict[str, str]: ...
    def chat_url(self, base_url: str) -> str: ...
    def generate_url(self, base_url: str) -> str: ...
    def embedding_url(self, base_url: str) -> str: ...
    def chat_payload(self, model: str, messages: list[dict], stream: bool, temperature: float) -> dict: ...
    def generate_payload(self, model: str, prompt: str, temperature: float) -> dict: ...
    def embedding_payload(self, model: str, texts: list[str]) -> dict: ...
    def extract_usage(self, data: dict) -> tuple[int, int]: ...
    def extract_chat_content(self, data: dict) -> str: ...
    def extract_stream_chunk(self, data: dict) -> tuple[str, bool]: ...
    def extract_embeddings(self, data: dict) -> list[list[float]]: ...


class OpenAICompatibleAdapter:
    uses_native_embedding_batch = False

    def headers(self, api_key: str) -> dict[str, str]:
        if not api_key:
            raise RuntimeError("LLM_API_KEY is not configured")
        return {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    def chat_url(self, base_url: str) -> str:
        return f"{base_url}/chat/completions"

    def generate_url(self, base_url: str) -> str:
        return self.chat_url(base_url)

    def embedding_url(self, base_url: str) -> str:
        return f"{base_url}/embeddings"

    def chat_payload(self, model: str, messages: list[dict], stream: bool, temperature: float) -> dict:
        return {"model": model, "messages": messages, "stream": stream, "temperature": temperature}

    def generate_payload(self, model: str, prompt: str, temperature: float) -> dict:
        return self.chat_payload(model, [{"role": "user", "content": prompt}], False, temperature)

    def embedding_payload(self, model: str, texts: list[str]) -> dict:
        return {"model": model, "input": texts}

    def extract_usage(self, data: dict) -> tuple[int, int]:
        usage = data.get("usage") or {}
        return usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0

    def extract_chat_content(self, data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "") or ""

    def extract_stream_chunk(self, data: dict) -> tuple[str, bool]:
        choices = data.get("choices") or []
        if not choices:
            return "", False
        return (choices[0].get("delta") or {}).get("content") or "", False

    def extract_embeddings(self, data: dict) -> list[list[float]]:
        return [row.get("embedding", []) for row in data.get("data") or []]


class OllamaAdapter:
    uses_native_embedding_batch = True

    def headers(self, api_key: str) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def chat_url(self, base_url: str) -> str:
        return f"{base_url}/api/chat"

    def generate_url(self, base_url: str) -> str:
        return f"{base_url}/api/generate"

    def embedding_url(self, base_url: str) -> str:
        return f"{base_url}/api/embed"

    def chat_payload(self, model: str, messages: list[dict], stream: bool, temperature: float) -> dict:
        return {"model": model, "messages": messages, "stream": stream, "options": {"temperature": temperature}}

    def generate_payload(self, model: str, prompt: str, temperature: float) -> dict:
        return {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": temperature}}

    def embedding_payload(self, model: str, texts: list[str]) -> dict:
        return {"model": model, "input": texts}

    def extract_usage(self, data: dict) -> tuple[int, int]:
        return data.get("prompt_eval_count", 0) or 0, data.get("eval_count", 0) or 0

    def extract_chat_content(self, data: dict) -> str:
        return data.get("message", {}).get("content", "")

    def extract_stream_chunk(self, data: dict) -> tuple[str, bool]:
        return data.get("message", {}).get("content", "") or "", bool(data.get("done", False))

    def extract_embeddings(self, data: dict) -> list[list[float]]:
        embeddings = data.get("embeddings", [])
        if not embeddings:
            return []
        return embeddings if isinstance(embeddings[0], list) else [embeddings]


_OPENAI_COMPATIBLE = OpenAICompatibleAdapter()
_OLLAMA = OllamaAdapter()


def provider_adapter(provider: str) -> LLMProviderAdapter:
    """Return the concrete adapter; unknown providers use the OpenAI contract."""
    return _OLLAMA if provider == "ollama" else _OPENAI_COMPATIBLE
