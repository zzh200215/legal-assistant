from app.core.config import get_settings


PLACEHOLDER_API_KEYS = {
    "",
    "your-dashscope-api-key",
    "your-api-key",
    "replace-with-real-key",
    "replace-me",
}


def is_placeholder_api_key(value: str | None) -> bool:
    normalized = (value or "").strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered in PLACEHOLDER_API_KEYS:
        return True
    return lowered.startswith("your-") and "key" in lowered


def ensure_eval_llm_ready() -> None:
    settings = get_settings()
    if settings.LLM_PROVIDER == "ollama":
        return
    if is_placeholder_api_key(settings.LLM_API_KEY):
        raise RuntimeError(
            "LLM_API_KEY 仍是占位值。请在 .env 中填入可用的 DashScope 百炼 API Key 后，再运行评测索引或实验脚本。"
        )


def set_eval_seed(seed: int = 42) -> None:
    """固定评测随机种子，保证同版本可复现（脱敏/采样/任何随机化均由此控制）。

    评测确定性基线：LLM temperature=0 + dataset fingerprint（sha256）+ 本种子。
    所有采样器（eval/stratified_sampler.py）必须接收并尊重 seed 参数。
    """
    import random

    random.seed(seed)

