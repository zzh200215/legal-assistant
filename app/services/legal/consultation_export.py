"""咨询结果导出：把结果卡内容渲染成纯 Markdown。

只做确定性拼装，不调用模型、不写文件；HTTP 层负责鉴权、审计与下载头。
"""

from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.models.legal import LegalConsultation, LegalConsultationExport
from app.services.legal.legal_workspace_service import serialize_workspace_row

# 默认保留策略：90 天 + 每条咨询最多 20 份；可被 settings 覆盖。
EXPORT_RETENTION_DAYS = 90
EXPORT_MAX_PER_CONSULTATION = 20


def export_retention_limits(retention_days: int | None = None,
                            max_records: int | None = None) -> tuple[int, int]:
    settings = get_settings()
    return (
        int(retention_days if retention_days is not None
            else settings.LEGAL_CONSULTATION_EXPORT_RETENTION_DAYS),
        int(max_records if max_records is not None
            else settings.LEGAL_CONSULTATION_EXPORT_MAX_PER_CONSULTATION),
    )

STATUS_LABELS = {
    "draft": "草稿",
    "pending_review": "待审核",
    "needs_lawyer_review": "需律师审核",
    "lawyer_approved": "律师通过",
    "needs_facts": "待补充事实",
    "returned_for_facts": "退回补充",
    "offline_consultation": "转线下",
    "archived": "已归档",
}
RISK_LABELS = {"low": "低", "medium": "中", "high": "高", "critical": "严重"}


def _bullet_list(items: list, empty_text: str = "暂无") -> str:
    values = [str(item).strip() for item in (items or []) if str(item).strip()]
    if not values:
        return f"- {empty_text}"
    return "\n".join(f"- {value}" for value in values)


def _reference_lines(references: list) -> str:
    lines = []
    for ref in references or []:
        if not isinstance(ref, dict):
            continue
        title = ref.get("title") or "未命名法源"
        citation = ref.get("citation") or ""
        verification = ref.get("verification") if isinstance(ref.get("verification"), dict) else {}
        suffix = ""
        if verification.get("verified") is True:
            suffix = "（已核验）"
        elif verification.get("article_scope") == "missing":
            suffix = "（条号未找到）"
        elif verification.get("article_scope") == "no_corpus":
            suffix = "（法源缺条文）"
        lines.append(f"- {title} {citation}{suffix}".rstrip())
    return "\n".join(lines) if lines else "- 暂无"


def prune_consultation_exports(
    db: Session, consultation_id: int, *, now=None,
    retention_days: int | None = None, max_records: int | None = None,
) -> int:
    """按保留策略清理某条咨询的导出记录，返回删除数量。"""
    days, keep = export_retention_limits(retention_days, max_records)
    moment = now or utc_now()
    cutoff = moment - timedelta(days=days)
    deleted = (
        db.query(LegalConsultationExport)
        .filter(
            LegalConsultationExport.consultation_id == consultation_id,
            LegalConsultationExport.created_at < cutoff,
        )
        .delete(synchronize_session=False)
    )
    stale_ids = [
        row_id
        for (row_id,) in db.query(LegalConsultationExport.id)
        .filter(LegalConsultationExport.consultation_id == consultation_id)
        .order_by(LegalConsultationExport.id.desc())
        .offset(keep)
        .all()
    ]
    if stale_ids:
        deleted += (
            db.query(LegalConsultationExport)
            .filter(LegalConsultationExport.id.in_(stale_ids))
            .delete(synchronize_session=False)
        )
    return deleted


def prune_all_consultation_exports(
    db: Session, *, now=None,
    retention_days: int | None = None, max_records: int | None = None,
) -> dict:
    """定时任务入口：全表按「天数 + 每咨询份数」清理，返回删除统计。"""
    days, keep = export_retention_limits(retention_days, max_records)
    moment = now or utc_now()
    cutoff = moment - timedelta(days=days)
    deleted = (
        db.query(LegalConsultationExport)
        .filter(LegalConsultationExport.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    over_limit = (
        db.query(LegalConsultationExport.consultation_id, func.count(LegalConsultationExport.id))
        .group_by(LegalConsultationExport.consultation_id)
        .having(func.count(LegalConsultationExport.id) > keep)
        .all()
    )
    for consultation_id, _count in over_limit:
        stale_ids = [
            row_id
            for (row_id,) in db.query(LegalConsultationExport.id)
            .filter(LegalConsultationExport.consultation_id == consultation_id)
            .order_by(LegalConsultationExport.id.desc())
            .offset(keep)
            .all()
        ]
        if stale_ids:
            deleted += (
                db.query(LegalConsultationExport)
                .filter(LegalConsultationExport.id.in_(stale_ids))
                .delete(synchronize_session=False)
            )
    return {
        "deleted": int(deleted),
        "consultations_over_limit": len(over_limit),
        "retention_days": days,
        "max_records": keep,
    }


def build_consultation_markdown(row: LegalConsultation) -> str:
    """把一条咨询渲染为可归档/转发的 Markdown 文本。"""
    payload = serialize_workspace_row(row)
    status = payload.get("status") or "draft"
    risk = payload.get("risk_level") or "low"
    lines = [
        "# 法律咨询结果",
        "",
        f"**咨询问题：** {payload.get('question') or ''}",
        f"**当前状态：** {STATUS_LABELS.get(status, status)}",
        f"**风险等级：** {RISK_LABELS.get(risk, risk)}",
        f"**生成时间：** {payload.get('created_at') or ''}",
        f"**最近更新：** {payload.get('updated_at') or ''}",
    ]
    if payload.get("case_id"):
        lines.append(f"**关联案件：** #{payload['case_id']}")
    confidence = payload.get("confidence")
    if confidence is not None:
        lines.append(f"**置信度：** {confidence}%")
    lines.extend([
        "",
        "## 结论与建议",
        "",
        payload.get("advice") or "暂无",
        "",
        "## 已知事实",
        "",
        _bullet_list(payload.get("known_facts") or []),
        "",
        "## 待补充事实",
        "",
        _bullet_list(payload.get("missing_facts") or [], "无缺失"),
        "",
        "## 参考依据",
        "",
        _reference_lines(payload.get("references") or []),
    ])
    summary = payload.get("evidence_summary") or {}
    if summary:
        lines.extend([
            "",
            "## 证据可核验性",
            "",
            f"- 引用 {summary.get('references', 0)} 条，句级覆盖 "
            f"{summary.get('attributed', 0)}/{summary.get('sentences', 0)} 句",
            f"- 忠实度：{summary.get('faithfulness', 'unchecked')}",
            f"- 等级：{summary.get('level', 'none')}",
        ])
    lines.extend([
        "",
        "---",
        "",
        "> 本结果由 AI 辅助生成，仅供律师工作参考，不构成正式法律意见；"
        "最终结论需由执业律师复核。",
        "",
        f"<!-- content_version={payload.get('content_version', '')} -->",
        "",
    ])
    return "\n".join(lines)
