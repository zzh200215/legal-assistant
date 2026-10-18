"""视觉核查样例数据：为系统中心各 tab 提供真实后端可见的少量样本行。

用途：方向 B 视觉核查（真实后端截图）需要各 tab 有数据，而不是空态。
所有插入/更新行的文本字段都带「视觉核查样例」标注，便于日后清理：
    delete from operation_logs where detail like '%视觉核查样例%';
    delete from knowledge_bases where description like '%视觉核查样例%';
    delete from agent_approval_requests where approval_token like 'visual-check-%';
    delete from agent_runs where failure_reason like '%视觉核查样例%';
    delete from email_send_requests where error_message like '%视觉核查样例%';
    -- tool_call_logs 仅更新时间，不新增行
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta

sys.path.insert(0, r"D:\AI\llmXM\AI法律助手")

from sqlalchemy import text

from app.core.database import SessionLocal
from app.core.time import utc_now

NOW = utc_now()
ADMIN_ID = 3


def main() -> None:
    with SessionLocal() as s:
        # 1) 工具健康：把已有的 5 条 tool_call_logs 挪到近 30 天内，并制造 1 次失败
        rows = s.execute(text("select id, tool_name from tool_call_logs order by id")).all()
        for idx, (row_id, _tool) in enumerate(rows):
            s.execute(
                text(
                    "update tool_call_logs set created_at = :ts, duration_ms = :dur, status = :st where id = :id"
                ),
                {
                    "ts": NOW - timedelta(hours=2 * idx + 1),
                    "dur": 180 + idx * 65,
                    "st": "error" if idx == 1 else "success",
                    "id": row_id,
                },
            )

        # 2) 知识库：2 个空间
        for name, category, scope, desc in (
            ("劳动争议知识库", "labor", "org", "视觉核查样例：劳动合同、仲裁与工伤口径。"),
            ("合同范本库", "contract", "private", "视觉核查样例：常用合同模板与审查要点。"),
        ):
            s.execute(
                text(
                    """insert into knowledge_bases
                       (user_id, name, description, category, permission_scope, created_at, updated_at, organization_id, department_id)
                       values (:uid, :name, :desc, :cat, :scope, :ts, :ts, 5, null)"""
                ),
                {"uid": ADMIN_ID, "name": name, "desc": desc, "cat": category, "scope": scope, "ts": NOW},
            )

        # 3) 审批台：2 条待审批
        for tool, risk, params, token in (
            ("send_email", "high", {"recipient": "client@example.com", "subject": "仲裁申请书送达"}, "visual-check-1"),
            ("delete_document", "medium", {"document_id": 12, "reason": "重复上传"}, "visual-check-2"),
        ):
            s.execute(
                text(
                    """insert into agent_approval_requests
                       (user_id, tool_name, agent_type, input_params, risk_level, status, approval_token,
                        created_at, expires_at, policy_version, data_scope)
                       values (:uid, :tool, 'legal_assistant', :params, :risk, 'pending', :token,
                               :ts, :exp, 'visual-check', 'case')"""
                ),
                {
                    "uid": ADMIN_ID,
                    "tool": tool,
                    "params": json.dumps(params, ensure_ascii=False),
                    "risk": risk,
                    "token": token,
                    "ts": NOW - timedelta(hours=3),
                    "exp": NOW + timedelta(days=1),
                },
            )

        # 4) 失败 Agent 运行：告警来源之一
        s.execute(
            text(
                """insert into agent_runs (user_id, goal, status, error, failure_reason, total_steps, created_at, completed_at, organization_id, agent_type)
                   values (:uid, :goal, 'error', :err, :err, 3, :ts, :ts, 5, 'legal_assistant')"""
            ),
            {
                "uid": ADMIN_ID,
                "goal": "视觉核查样例：整理仲裁证据清单",
                "err": "视觉核查样例：模型调用超时（llm timeout）",
                "ts": NOW - timedelta(hours=6),
            },
        )

        # 5) 异步任务失败日志：告警来源之一
        for action, detail, hours in (
            ("document_parse_failed", "视觉核查样例：PDF 解析失败（扫描件无文本层）", 4),
            ("contract_review_failed", "视觉核查样例：合同审查超时（条款过长）", 26),
            ("agent_run_failed", "视觉核查样例：Agent 执行中断（工具调用异常）", 70),
        ):
            s.execute(
                text(
                    """insert into operation_logs (user_id, module, action, target_type, target_id, detail, ip_address, created_at)
                       values (:uid, 'async_task', :action, 'task_run', :tid, :detail, '127.0.0.1', :ts)"""
                ),
                {"uid": ADMIN_ID, "action": action, "tid": hours, "detail": detail, "ts": NOW - timedelta(hours=hours)},
            )

        # 6) 外发失败邮件：告警来源之一
        for recipient, subject, err, hours in (
            ("client@example.com", "仲裁申请书（盖章版）", "视觉核查样例：SMTP 550 mailbox unavailable", 5),
            ("opposing@example.com", "调解方案确认函", "视觉核查样例：SMTP 421 服务暂不可用", 40),
        ):
            s.execute(
                text(
                    """insert into email_send_requests
                       (draft_id, smtp_connector_id, user_id, organization_id, recipient, subject, content_hash, idempotency_key, status,
                        dlp_status, attempt, max_attempts, error_message, created_at, updated_at)
                       values (1, 1, :uid, 5, :rcpt, :subject, :hash, :key, 'failed', 'passed', 1, 3, :err, :ts, :ts)"""
                ),
                {
                    "uid": ADMIN_ID,
                    "rcpt": recipient,
                    "subject": subject,
                    "hash": f"visual-check-{hours}",
                    "key": f"visual-check-{hours}",
                    "err": err,
                    "ts": NOW - timedelta(hours=hours),
                },
            )

        s.commit()

        for table, sql in (
            ("tool_call_logs", "select count(*), min(created_at), max(created_at) from tool_call_logs"),
            ("knowledge_bases", "select count(*) from knowledge_bases"),
            ("agent_approval_requests", "select count(*) from agent_approval_requests"),
            ("agent_runs", "select count(*) from agent_runs where status='error'"),
            ("operation_logs(async_task failed)", "select count(*) from operation_logs where module='async_task' and action like '%_failed'"),
            ("email_send_requests(failed)", "select count(*) from email_send_requests where status='failed'"),
        ):
            print(f"{table}: {tuple(s.execute(text(sql)).one())}")


if __name__ == "__main__":
    main()
