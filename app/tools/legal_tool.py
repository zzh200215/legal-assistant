"""Legal domain Agent tools for MCP registry.

Tools:
- legal_consultation_tool: 法律咨询（分类、事实提取、风险评估、法规检索）
- legal_contract_review_tool: 合同智能审查（条款风险识别）
- legal_draft_tool: 法律文书草稿生成
"""

from app.mcp.tool_contract import ToolContract
from app.services.legal.legal_service import (
    DRAFT_FIELDS,
    consultation_payload,
    draft_content,
    ensure_demo_sources,
    review_contract,
)
from app.tools.base import BaseAgentTool, tool_error, tool_success


class LegalConsultationTool(BaseAgentTool):
    name = "legal_consultation_tool"
    description = "法律咨询辅助：对用户描述的法律问题进行分类、提取已知事实、识别待补充事实、评估风险等级，检索相关法规并给出一般性建议。不预测裁判结果。"
    auto_context_fields = ("user_id", "db")
    contract = ToolContract(
        name="legal_consultation_tool", read_only=True, requires_approval=False,
        side_effect="llm_analysis", audit_level="summary",
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "用户描述的法律问题"},
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["question", "user_id"],
    }

    async def run(self, question: str, user_id: int, db) -> dict:
        from app.models.legal import LegalSource
        from app.services.rag.rag_service import rag_service

        ensure_demo_sources(db, user_id)
        sources = db.query(LegalSource).filter(
            LegalSource.user_id == user_id, LegalSource.status == "active"
        ).all()

        # RAG 法规检索：从知识库中找到与问题相关的法规片段（仅检索当前用户已授权文档）
        rag_chunks = []
        try:
            from app.models.user import User
            from app.services.documents.document_governance_service import document_governance_service

            user = db.query(User).filter(User.id == user_id).first()
            authorized_ids = document_governance_service.list_accessible_document_ids(
                db=db,
                user_id=user_id,
                role=user.role if user else None,
                organization_id=user.organization_id if user else None,
                department_id=user.department_id if user else None,
            )
            rag_chunks = await rag_service.search_async(
                question,
                top_k=3,
                user_id=user_id,
                authorized_document_ids=authorized_ids,
            )
        except Exception:
            pass

        category, known, missing, refs, advice, risk, status = await consultation_payload(
            question, sources, user_id=user_id, db=db
        )

        # 如果 RAG 有检索结果，补充到 references
        rag_refs = []
        for chunk in rag_chunks[:3]:
            rag_refs.append({
                "source_id": chunk.get("document_id"),
                "title": chunk.get("document_title", "法规检索结果"),
                "citation": chunk.get("chunk_text", "")[:100],
                "snippet": chunk.get("chunk_text", "")[:200],
            })

        return tool_success(
            "法律咨询完成",
            {
                "category": category,
                "known_facts": known,
                "missing_facts": missing,
                "references": refs,
                "rag_references": rag_refs,
                "advice": advice,
                "risk_level": risk,
                "status": status,
            },
        )


class LegalContractReviewTool(BaseAgentTool):
    name = "legal_contract_review_tool"
    description = "合同智能审查：对合同内容逐条款审查，识别风险条款、缺失条款，给出审查意见和修改建议。不替代律师最终审查。"
    auto_context_fields = ("user_id",)
    contract = ToolContract(
        name="legal_contract_review_tool", read_only=True, requires_approval=False,
        side_effect="llm_analysis", audit_level="summary",
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "合同全文或主要条款内容"},
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["content", "user_id"],
    }

    async def run(self, content: str, user_id: int) -> dict:
        risks, summary = await review_contract(content, user_id=user_id)
        high_count = sum(1 for item in risks if item.get("risk_level") == "high")
        return tool_success(
            "合同审查完成",
            {
                "risks": risks,
                "summary": summary,
                "high_risk_count": high_count,
                "total_risks": len(risks),
            },
        )


class LegalDraftTool(BaseAgentTool):
    name = "legal_draft_tool"
    description = "法律文书草稿生成：根据文书类型和字段信息生成法律文书草稿。支持劳动仲裁申请书、民间借贷起诉状、消费纠纷投诉书、补充协议。缺失字段用【待补充】标注。"
    auto_context_fields = ("user_id",)
    contract = ToolContract(
        name="legal_draft_tool", read_only=True, requires_approval=False,
        side_effect="llm_generation", audit_level="summary",
    )
    parameters = {
        "type": "object",
        "properties": {
            "document_type": {
                "type": "string",
                "description": "文书类型：labor_arbitration_application / private_lending_complaint / consumer_complaint / supplementary_agreement",
            },
            "fields": {
                "type": "object",
                "description": "文书字段键值对，如 {'申请人': '张三', '仲裁请求': '...'}",
            },
            "user_id": {"type": "integer", "description": "当前用户 ID，由 Agent 自动注入"},
        },
        "required": ["document_type", "user_id"],
    }

    async def run(self, document_type: str, user_id: int, fields: dict | None = None) -> dict:
        fields = fields or {}
        if document_type not in DRAFT_FIELDS:
            return tool_error(
                f"暂不支持文书类型: {document_type}",
                data={"supported_types": list(DRAFT_FIELDS.keys())},
            )
        missing = [f for f in DRAFT_FIELDS[document_type] if not fields.get(f)]
        content = await draft_content(document_type, fields, missing, user_id=user_id)
        return tool_success(
            "文书草稿生成完成",
            {
                "document_type": document_type,
                "content": content,
                "missing_fields": missing,
                "has_missing": bool(missing),
            },
        )
