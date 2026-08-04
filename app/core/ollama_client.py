from app.core.llm_client import LLMClient, llm_client

# Backward-compatible aliases kept temporarily while the codebase migrates off
# the old provider-specific naming.
OllamaClient = LLMClient
ollama_client = llm_client
