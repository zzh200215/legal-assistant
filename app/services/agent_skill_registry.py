"""Reusable, policy-bound skills for the controlled Agent harness.

Skills are intentionally declarative. They describe a stable business task,
its preferred worker plan, and expected artifacts. Tool authorization remains
enforced by ``app.mcp.permissions`` at execution time.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SKILL_REGISTRY_VERSION = "legal_workbench_skills_v1"

AGENT_SKILLS: tuple[dict[str, Any], ...] = (
    {
        "skill_id": "legal_consultation",
        "name": "法律咨询与法源溯源",
        "description": "梳理法律问题、事实缺口、风险等级和可追溯的参考依据。",
        "keywords": ("法律咨询", "仲裁", "劳动争议", "借贷", "消费者", "赔偿"),
        "worker_plan": ("legal_compliance_agent",),
        "expected_artifacts": ("legal_advice", "legal_sources"),
        "evidence_required": True,
    },
    {
        "skill_id": "contract_review",
        "name": "合同审查与风险提示",
        "description": "识别合同条款风险，输出原文定位、风险说明和修改建议。",
        "keywords": ("合同审查", "审查合同", "违约", "条款", "付款", "验收"),
        "worker_plan": ("legal_compliance_agent",),
        "expected_artifacts": ("contract_review", "risk_items"),
        "evidence_required": True,
    },
    {
        "skill_id": "legal_draft",
        "name": "法律文书草稿",
        "description": "基于结构化事实生成法律文书草稿，并明确待补充字段。",
        "keywords": ("起诉状", "仲裁申请", "投诉书", "补充协议", "法律文书", "文书草稿"),
        "worker_plan": ("legal_compliance_agent",),
        "expected_artifacts": ("legal_draft",),
        "evidence_required": False,
    },
    {
        "skill_id": "document_evidence_review",
        "name": "文档证据与风险审阅",
        "description": "检索指定文档，提取摘要、风险、冲突或关键证据定位。",
        "keywords": ("文档风险", "文档摘要", "文档冲突", "证据定位", "总结文档"),
        "worker_plan": ("knowledge_agent",),
        "expected_artifacts": ("document", "evidence"),
        "evidence_required": True,
    },
    {
        "skill_id": "meeting_to_task",
        "name": "会议纪要转行动项",
        "description": "先提取会议纪要和行动项，再在人工审批后创建内部任务。",
        "keywords": ("会议纪要", "会议行动项", "会议任务", "总结会议"),
        "worker_plan": ("meeting_agent", "workflow_agent"),
        "expected_artifacts": ("meeting", "task"),
        "evidence_required": True,
    },
    {
        "skill_id": "project_risk_assessment",
        "name": "项目风险评估",
        "description": "基于项目资料、会议和任务状态识别里程碑、依赖和交付风险。",
        "keywords": ("项目风险", "里程碑", "项目延期", "项目依赖", "交付风险"),
        "worker_plan": ("project_agent",),
        "expected_artifacts": ("project_risk_register",),
        "evidence_required": True,
    },
    {
        "skill_id": "data_analysis_report",
        "name": "受控数据分析报告",
        "description": "基于白名单数据源执行只读查询并形成指标或分析报告。",
        "keywords": ("数据分析", "销售日报", "查询数据库", "sql", "指标报告"),
        "worker_plan": ("data_agent",),
        "expected_artifacts": ("data_report",),
        "evidence_required": True,
    },
    {
        "skill_id": "communication_draft",
        "name": "业务沟通草稿",
        "description": "将已确认的上游结论转化为邮件或通知草稿，不直接外发。",
        "keywords": ("邮件草稿", "写邮件", "通知草稿", "催办邮件"),
        "worker_plan": ("communication_agent",),
        "expected_artifacts": ("email_draft",),
        "evidence_required": True,
    },
)


def list_agent_skills() -> list[dict[str, Any]]:
    """Return registry entries without exposing mutable global state."""
    return [deepcopy(skill) for skill in AGENT_SKILLS]


def get_agent_skill(skill_id: str) -> dict[str, Any] | None:
    normalized = (skill_id or "").strip()
    for skill in AGENT_SKILLS:
        if skill["skill_id"] == normalized:
            return deepcopy(skill)
    return None


def resolve_agent_skill(goal: str) -> dict[str, Any] | None:
    """Choose the highest-specificity skill for a goal using deterministic cues."""
    normalized = (goal or "").strip().lower()
    if not normalized:
        return None

    matched: list[tuple[int, int, dict[str, Any], list[str]]] = []
    for index, skill in enumerate(AGENT_SKILLS):
        keywords = [keyword for keyword in skill["keywords"] if keyword.lower() in normalized]
        if keywords:
            matched.append((len(keywords), -index, skill, keywords))
    if not matched:
        return None

    _, _, selected, keywords = max(matched, key=lambda item: (item[0], item[1]))
    payload = deepcopy(selected)
    payload["matched_keywords"] = keywords
    payload["match_mode"] = "deterministic_keyword"
    return payload
