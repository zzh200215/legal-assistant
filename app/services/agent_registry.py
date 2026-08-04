"""Canonical registry for the domain agents used by the orchestration layer.

The registry describes stable role contracts. Tool execution permissions remain
enforced by ``app.mcp.permissions``; this module is intentionally metadata-only.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.mcp.permissions import CANONICAL_AGENT_TYPES, allowed_tools_for


AGENT_REGISTRY_VERSION = "enterprise_experts_v1"
TASK_PROTOCOL_VERSION = "agent_task_v1"

# The Supervisor is an orchestration role, not a Worker. It intentionally has
# no MCP tools and is returned separately by the registry API for the UI.
SUPERVISOR_REGISTRATION: dict[str, Any] = {
    "agent_type": "supervisor_agent",
    "label": "律智检总管 Agent",
    "description": "理解法律业务目标，选择专家 Worker、安排依赖与并行、汇总产物；不替代领域专家输出专业法律结论，也不直接执行业务写操作。",
    "capabilities": ["intent_routing", "expert_orchestration", "artifact_aggregation"],
    "input_contract": "用户目标、可用专家角色、权限和已完成产物",
    "output_contract": "结构化专家计划、交接顺序、风险等级和综合结论",
    "forbidden": "直接调用业务工具、编造专业结论或绕过审批",
    "execution_mode": "orchestration_only",
    "allowed_tools": [],
}

AGENT_REGISTRY: dict[str, dict[str, Any]] = {
    "knowledge_agent": {
        "label": "知识 Agent",
        "description": "只负责企业知识检索、证据聚合、引用溯源和基于证据的回答；不执行外部写操作。",
        "capabilities": ["knowledge_retrieval", "evidence_aggregation", "cited_answer", "document_risk_review"],
        "input_contract": "用户问题、权限上下文、知识库范围",
        "output_contract": "带来源定位的回答、证据列表或明确拒答",
        "forbidden": "创建任务、发送邮件、执行 SQL 或调用任何写工具",
        "execution_mode": "read_only",
    },
    "meeting_agent": {
        "label": "会议 Agent",
        "description": "只负责音频或转写内容的会议理解，输出纪要、决策和结构化行动项；不直接创建任务。",
        "capabilities": ["meeting_summary", "decision_extraction", "action_item_extraction"],
        "input_contract": "会议 ID、音频转写或已有会议上下文",
        "output_contract": "结构化纪要、决策、风险和行动项",
        "forbidden": "创建任务、发送通知或修改外部业务状态",
        "execution_mode": "read_only",
    },
    "data_agent": {
        "label": "数据 Agent",
        "description": "只负责白名单 Schema 理解、Text-to-SQL、只读查询、指标计算和分析报告。",
        "capabilities": ["schema_understanding", "text_to_sql", "metric_calculation", "data_report"],
        "input_contract": "分析目标、白名单数据源和允许的 Schema",
        "output_contract": "受控 SQL、查询结果、指标、图表数据或分析报告",
        "forbidden": "执行写 SQL、访问非白名单表或绕过字段脱敏策略",
        "execution_mode": "controlled_read_only",
    },
    "project_agent": {
        "label": "项目管理专家",
        "description": "负责基于项目资料、会议和任务状态识别里程碑、依赖、交付风险与待办缺口；只形成项目建议，不直接调整项目基线或分配任务。",
        "capabilities": ["project_status_review", "milestone_risk_review", "dependency_analysis", "risk_register_draft"],
        "input_contract": "项目相关文档、会议纪要、任务状态和用户权限",
        "output_contract": "项目状态摘要、里程碑风险、依赖影响、风险台账草稿和建议行动项",
        "forbidden": "创建或分派任务、修改项目计划、承诺交付日期或绕过审批",
        "execution_mode": "read_only",
    },
    "legal_compliance_agent": {
        "label": "法律合规专家",
        "description": "负责法律咨询分类、事实提取、合同条款审查、风险分级、文书草稿生成与证据溯源；输出审查意见和一般性建议，而不是法律定论。",
        "capabilities": ["legal_consultation", "contract_review", "clause_risk_review", "legal_draft_generation", "evidence_backed_comparison"],
        "input_contract": "法律问题描述、合同内容、文书类型与字段、适用范围和用户权限",
        "output_contract": "问题分类、事实清单、风险等级、审查意见、文书草稿、参考法源和免责声明",
        "forbidden": "签署或修改合同、替代执业法律意见、发送外部文件或创建业务任务",
        "execution_mode": "read_only",
    },
    "communication_agent": {
        "label": "沟通写作专家",
        "description": "负责将已经确认的项目、会议或法务结论转化为面向指定受众的邮件与通知草稿，并保持口径、语气和敏感信息边界。",
        "capabilities": ["email_drafting", "stakeholder_communication", "status_update_writing"],
        "input_contract": "已确认的上游结论、受众、沟通目的和可用模板",
        "output_contract": "可审阅的邮件或通知草稿、建议主题和待确认收件人",
        "forbidden": "编造上游事实、直接发送外部邮件、绕过 DLP 或审批",
        "execution_mode": "draft_only",
    },
    "workflow_agent": {
        "label": "流程执行 Agent",
        "description": "负责将已确认的专家产物落为任务等内部业务动作，并在审批后恢复执行；它是受控执行层，不承担领域判断或沟通写作职责。",
        "capabilities": ["task_execution", "meeting_action_execution", "approval_resume"],
        "input_contract": "已确认的执行目标、结构化上游产物和审批上下文",
        "output_contract": "工具执行结果、业务产物引用和可审计状态",
        "forbidden": "编造业务参数、绕过人工审批或替代领域 Agent 生成专业结论",
        "execution_mode": "controlled_side_effect",
    },
}


def get_agent_registration(agent_type: str) -> dict[str, Any] | None:
    """Return a copy of one canonical role contract with its current tool ACL."""
    registration = AGENT_REGISTRY.get(agent_type)
    if not registration:
        return None
    payload = deepcopy(registration)
    payload["agent_type"] = agent_type
    payload["allowed_tools"] = sorted(allowed_tools_for(agent_type))
    return payload


def get_supervisor_registration() -> dict[str, Any]:
    """Return the tool-less orchestration role shown in Agent Studio."""
    return deepcopy(SUPERVISOR_REGISTRATION)


def list_agent_registrations() -> list[dict[str, Any]]:
    """Return canonical agents in orchestration order, excluding legacy aliases."""
    return [item for agent_type in CANONICAL_AGENT_TYPES if (item := get_agent_registration(agent_type))]
