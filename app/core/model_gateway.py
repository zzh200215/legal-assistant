"""供应商无关的模型网关门面。

业务代码统一从这里获取模型能力（chat/generate/vision/embedding），
实现位于 app.core.llm_client.ModelGateway。LLMClient/llm_client 为兼容别名。
"""

from app.core.llm_client import LLMClient, ModelGateway, llm_client, model_gateway

__all__ = ["ModelGateway", "model_gateway", "LLMClient", "llm_client"]
