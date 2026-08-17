import asyncio

from app.mcp.tool_contract import ToolContract
from app.services.documents.analysis_service import analysis_service
from app.services.documents.document_service import document_service
from app.services.rag.rag_service import rag_service
from app.tools.base import BaseAgentTool, tool_error, tool_success


class DocumentSearchTool(BaseAgentTool):
    name = "document_search_tool"
    description = "根据问题检索文档知识库，返回相关文档片段，可选限定 document_id。"
    auto_context_fields = ("user_id",)
    contract = ToolContract(
        name="document_search_tool", read_only=True, requires_approval=False,
        side_effect="reads_knowledge_base", audit_level="summary",
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或问题"},
            "document_id": {"type": "integer", "description": "可选，限定搜索的文档 ID"},
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["query", "user_id"],
    }

    async def run(self, query: str, user_id: int, document_id: int | None = None) -> dict:
        chunks = await asyncio.to_thread(rag_service.search, query, document_id, 5, user_id)
        return tool_success(
            f"检索到 {len(chunks)} 条相关片段",
            {
                "query": query,
                "document_id": document_id,
                "chunks": chunks,
            },
        )


class DocumentSummaryTool(BaseAgentTool):
    name = "document_summary_tool"
    description = "根据文档 ID 生成摘要，提取核心信息。"
    auto_context_fields = ("user_id", "db")
    contract = ToolContract(
        name="document_summary_tool", read_only=True, requires_approval=False,
        side_effect="reads_document", audit_level="summary",
    )
    parameters = {
        "type": "object",
        "properties": {
            "document_id": {"type": "integer", "description": "文档 ID"},
            "max_length": {"type": "integer", "description": "摘要最大长度，默认 500", "default": 500},
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["document_id", "user_id"],
    }

    async def run(self, document_id: int, user_id: int, db, max_length: int = 500) -> dict:
        try:
            raw_text = await asyncio.to_thread(document_service.summarize, document_id, db, user_id)
            summary = await analysis_service.summarize_document(raw_text, max_length=max_length)
            return tool_success(
                "文档摘要已生成",
                {
                    "document_id": document_id,
                    "summary": summary,
                    "max_length": max_length,
                },
            )
        except Exception as e:
            return tool_error("文档摘要生成失败", str(e), {"document_id": document_id})


class DocumentRiskTool(BaseAgentTool):
    name = "document_risk_tool"
    description = "根据文档 ID 提取结构化风险点，返回标题、说明、证据、严重程度和建议动作。"
    auto_context_fields = ("user_id", "db")
    contract = ToolContract(
        name="document_risk_tool", read_only=True, requires_approval=False,
        side_effect="reads_document", audit_level="summary",
    )
    parameters = {
        "type": "object",
        "properties": {
            "document_id": {"type": "integer", "description": "文档 ID"},
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["document_id", "user_id"],
    }

    async def run(self, document_id: int, user_id: int, db) -> dict:
        try:
            risks = await document_service.extract_risks(document_id, db, user_id=user_id)
            return tool_success(
                f"提取到 {len(risks)} 条风险点",
                {
                    "document_id": document_id,
                    "risks": risks,
                },
            )
        except Exception as e:
            return tool_error("文档风险提取失败", str(e), {"document_id": document_id})


class DocumentConflictTool(BaseAgentTool):
    name = "document_conflict_tool"
    description = "对两到五份文档中的日期、金额和负责人进行交叉核对，返回带原文定位的事实冲突；只读，不创建任务。"
    auto_context_fields = ("user_id", "db")
    contract = ToolContract(
        name="document_conflict_tool", read_only=True, requires_approval=False,
        side_effect="reads_document", audit_level="summary",
    )
    parameters = {
        "type": "object",
        "properties": {
            "document_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 2,
                "maxItems": 5,
                "description": "需要核对的文档 ID 列表，2 到 5 份",
            },
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["document_ids", "user_id"],
    }

    async def run(self, document_ids: list[int], user_id: int, db) -> dict:
        try:
            result = await document_service.compare(document_ids, db, user_id=user_id)
            conflict_analysis = (result.get("comparison") or {}).get("conflict_analysis") or {}
            conflicts = conflict_analysis.get("conflicts") or []
            return tool_success(
                f"完成 {len(document_ids)} 份文档核对，发现 {len(conflicts)} 条事实冲突",
                {
                    "document_ids": document_ids,
                    "conflicts": conflicts,
                    "conflict_analysis": conflict_analysis,
                },
            )
        except Exception as e:
            return tool_error("跨文档冲突检测失败", str(e), {"document_ids": document_ids})
