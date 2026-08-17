"""Task 层：legal_tasks 剩余任务直调补测（门户链接扫描/合同到期/开放审查/条款解析/审批超时）。

覆盖 app/tasks/legal_tasks.py：
- scan_expired_portal_links_task：过期/即将到期通知 + 去重 + 永久链接跳过；
- scan_contract_expiry_alerts_task：90/30/7 天预警窗口 + 去重 + 合同状态过滤；
- recover_queued_open_contract_reviews_task：排队超时过期 / 重新派发；
- process_open_contract_review_task：skip/取消/缺输入/成功/失败分支；
- parse_contract_versions_task：空文本失败 / 条款拆分 / 陈旧 parsing 重扫；
- check_legal_approval_timeouts_task：步骤超时 + 整链超时推进。
"""

import json
import unittest
from datetime import timedelta
from unittest.mock import ANY, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.core.time import utc_now
from app.models.legal import LegalApprovalChain, LegalApprovalStep
from app.models.legal_contract import LegalContract, LegalContractClause, LegalContractMilestone, LegalContractVersion
from app.models.legal_notifications import LegalNotificationEvent
from app.models.legal_platform import LegalAsyncJob, LegalAsyncJobInput
from app.models.legal_portal import LegalPortalLink
from app.models.org import Organization
from app.models.user import User
from app.tasks.legal_tasks import (
    check_legal_approval_timeouts_task,
    parse_contract_versions_task,
    process_open_contract_review_task,
    recover_queued_open_contract_reviews_task,
    scan_contract_expiry_alerts_task,
    scan_expired_portal_links_task,
)


def _engine():
    return create_engine(
        "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


class LegalTasksResilienceTests(unittest.TestCase):
    def setUp(self):
        self.engine = _engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.db = self.Session()
        self.org = Organization(name="LegalTasks", code="LGT")
        self.db.add(self.org)
        self.db.commit()
        self.user = User(username="lt", email="lt@example.com", hashed_password="h",
                         role="user", organization_id=self.org.id)
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self._session_patch = patch("app.tasks.legal_tasks.SessionLocal", self.Session)
        self._session_patch.start()
        self._heartbeat = patch("app.tasks.legal_tasks._record_beat_heartbeat")
        self._heartbeat.start()
        self._redis = patch(
            "app.tasks.runtime.redis.from_url",
            side_effect=RuntimeError("redis unavailable in unit tests"),
        )
        self._redis.start()

    def tearDown(self):
        self._redis.stop()
        self._heartbeat.stop()
        self._session_patch.stop()
        self.db.close()
        self.engine.dispose()

    def _notify_count(self):
        return self.db.query(LegalNotificationEvent).count()

    # ── scan_expired_portal_links_task ──────────────────────────────────────
    def _link(self, *, expires_at, status="active", is_permanent=0, seq=0) -> LegalPortalLink:
        link = LegalPortalLink(
            organization_id=self.org.id, case_id=1,
            token_hash=f"{seq:02d}" + "h" * 62, token_prefix="abcd1234",
            status=status, is_permanent=is_permanent,
            expires_at=expires_at, require_email_verification=1,
            created_by=self.user.id,
        )
        self.db.add(link)
        self.db.commit()
        self.db.refresh(link)
        return link

    def test_scan_expired_link_notifies_once_and_marks_expired(self):
        link = self._link(expires_at=utc_now() - timedelta(hours=1), seq=1)
        result = scan_expired_portal_links_task.run()
        self.assertEqual(result["expired_links"], 1)
        self.assertEqual(result["expired_notified"], 1)
        self.db.refresh(link)
        self.assertEqual(link.status, "expired")
        # 幂等：重复扫描不重复通知
        result2 = scan_expired_portal_links_task.run()
        self.assertEqual(result2["expired_notified"], 0)

    def test_scan_expiring_soon_link_notifies(self):
        self._link(expires_at=utc_now() + timedelta(days=2), seq=2)
        result = scan_expired_portal_links_task.run()
        self.assertEqual(result["expired_links"], 0)
        self.assertEqual(result["expiring_notified"], 1)

    def test_scan_permanent_and_far_future_links_skip(self):
        self._link(expires_at=utc_now() + timedelta(days=30), seq=3)
        self._link(expires_at=None, is_permanent=1, seq=4)
        result = scan_expired_portal_links_task.run()
        self.assertEqual(result["expired_notified"] + result["expiring_notified"], 0)
        self.assertEqual(self._notify_count(), 0)

    # ── scan_contract_expiry_alerts_task ────────────────────────────────────
    def _milestone(self, *, standard_date, contract=None, status="confirmed") -> LegalContractMilestone:
        contract = contract or LegalContract(
            organization_id=self.org.id, contract_no="CN-1", title="合同A", status="active",
            contract_type="other", created_by=self.user.id,
        )
        self.db.add(contract)
        self.db.commit()
        self.db.refresh(contract)
        ms = LegalContractMilestone(
            contract_id=contract.id, organization_id=self.org.id,
            milestone_type="expiry", status=status, standard_date=standard_date,
        )
        self.db.add(ms)
        self.db.commit()
        self.db.refresh(ms)
        return ms, contract

    def test_contract_expiry_alerts_for_due_offsets(self):
        ms, contract = self._milestone(standard_date=utc_now() + timedelta(days=20))
        result = scan_contract_expiry_alerts_task.run()
        self.assertEqual(result["created_alerts"], 2)  # 90/30 已到期，7 未到
        events = self.db.query(LegalNotificationEvent).all()
        bodies = {e.body for e in events}
        self.assertEqual(bodies, {f"milestone:{ms.id}:offset:90", f"milestone:{ms.id}:offset:30"})

    def test_contract_expiry_alert_idempotent_and_filters_terminated(self):
        ms, contract = self._milestone(standard_date=utc_now() + timedelta(days=20))
        self.assertEqual(scan_contract_expiry_alerts_task.run()["created_alerts"], 2)
        self.assertEqual(scan_contract_expiry_alerts_task.run()["created_alerts"], 0)
        # 终止合同不产生预警
        terminated = LegalContract(
            organization_id=self.org.id, contract_no="CN-2", title="合同B", status="terminated",
            contract_type="other", created_by=self.user.id,
        )
        self.db.add(terminated)
        self.db.commit()
        self._milestone(standard_date=utc_now() + timedelta(days=20), contract=terminated)
        self.assertEqual(scan_contract_expiry_alerts_task.run()["created_alerts"], 0)

    # ── recover_queued_open_contract_reviews_task ───────────────────────────
    def _async_job(self, *, status="queued", created_at=None) -> LegalAsyncJob:
        job = LegalAsyncJob(
            organization_id=self.org.id, job_type="open_contract_review", status=status,
            created_by=self.user.id, created_at=created_at or utc_now(),
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def test_recover_queued_reviews_expires_stale_and_redispatches(self):
        stale = self._async_job(created_at=utc_now() - timedelta(hours=7))
        fresh = self._async_job(created_at=utc_now())
        with patch("app.tasks.legal_tasks.process_open_contract_review_task") as process:
            process.delay.return_value = MagicMock(id="new-1")
            result = recover_queued_open_contract_reviews_task.run()
        self.assertEqual(result["expired"], 1)
        self.assertEqual(result["dispatched"], 1)
        self.db.refresh(stale)
        self.assertEqual(stale.status, "expired")
        process.delay.assert_called_once_with(fresh.id, headers=ANY)

    # ── process_open_contract_review_task ───────────────────────────────────
    def test_process_review_success_extracts_keywords(self):
        job = self._async_job(status="queued")
        self.db.add(LegalAsyncJobInput(
            job_id=job.id, app_id=1, request_fingerprint="f" * 64,
            title="租赁合同", content_ciphertext="本合同约定违约与赔偿条款",
        ))
        self.db.commit()
        result = process_open_contract_review_task.run(job.id)
        self.assertEqual(result, {"succeeded": True})
        self.db.refresh(job)
        self.assertEqual(job.status, "succeeded")
        summary = json.loads(job.result_summary)
        self.assertIn("违约", summary["risk_keywords"])

    def test_process_review_skips_processing_job(self):
        job = self._async_job(status="processing")
        result = process_open_contract_review_task.run(job.id)
        self.assertEqual(result, {"skipped": True})

    def test_process_review_cancel_requested(self):
        job = self._async_job(status="queued")
        job.cancel_requested = 1
        self.db.commit()
        result = process_open_contract_review_task.run(job.id)
        self.assertEqual(result, {"cancelled": True})
        self.db.refresh(job)
        self.assertEqual(job.status, "cancelled")

    def test_process_review_missing_input_fails(self):
        job = self._async_job(status="queued")
        result = process_open_contract_review_task.run(job.id)
        self.assertEqual(result, {"failed": True})
        self.db.refresh(job)
        self.assertEqual(job.status, "failed")

    # ── parse_contract_versions_task ────────────────────────────────────────
    def _contract_version(self, *, parse_status="uploading", text="", created_at=None) -> LegalContractVersion:
        ver = LegalContractVersion(
            organization_id=self.org.id, contract_id=1, version_no=1,
            parse_status=parse_status, text_snapshot=text,
            created_by=self.user.id, created_at=created_at or utc_now(),
        )
        self.db.add(ver)
        self.db.commit()
        self.db.refresh(ver)
        return ver

    def test_parse_versions_splits_clauses(self):
        ver = self._contract_version(text="第一条 总则\n内容一\n\n第二条 违约责任\n内容二\n\n第三条 保密\n内容三")
        result = parse_contract_versions_task.run()
        self.assertEqual(result["processed"], 1)
        self.db.refresh(ver)
        self.assertEqual(ver.parse_status, "ready")
        clauses = self.db.query(LegalContractClause).filter(
            LegalContractClause.contract_version_id == ver.id).all()
        self.assertEqual(len(clauses), 3)
        self.assertEqual(clauses[0].clause_no, "第一条")

    def test_parse_versions_empty_text_fails(self):
        ver = self._contract_version(text="  ")
        result = parse_contract_versions_task.run()
        self.assertEqual(result["processed"], 0)
        self.db.refresh(ver)
        self.assertEqual(ver.parse_status, "failed")

    def test_parse_versions_rescans_stale_parsing(self):
        # 陈旧判定按 created_at（模型无 updated_at 列；缺陷修复后语义）
        self._contract_version(parse_status="parsing", text="x",
                               created_at=utc_now() - timedelta(minutes=20))
        fresh = self._contract_version(parse_status="parsing", text="y",
                                       created_at=utc_now())
        result = parse_contract_versions_task.run()
        # 仅陈旧 parsing 被重扫（0 uploading + 1 stale）
        self.assertEqual(result["processed"], 1)
        self.db.refresh(fresh)
        self.assertEqual(fresh.parse_status, "parsing")  # 活跃的未被重复拾取

    # ── check_legal_approval_timeouts_task ──────────────────────────────────
    def _approval_chain(self, **kw) -> LegalApprovalChain:
        fields = {
            "organization_id": self.org.id, "target_type": "contract_review", "target_id": 1,
            "chain_type": "serial", "status": "in_progress", "current_step": 1,
            "created_by": self.user.id,
        }
        fields.update(kw)
        chain = LegalApprovalChain(**fields)
        self.db.add(chain)
        self.db.commit()
        self.db.refresh(chain)
        return chain

    def _approval_step(self, chain_id, *, step_order=1, status="pending", due_at=None) -> LegalApprovalStep:
        step = LegalApprovalStep(
            chain_id=chain_id, step_order=step_order, approver_id=self.user.id,
            status=status, due_at=due_at,
        )
        self.db.add(step)
        self.db.commit()
        return step

    def test_approval_timeout_marks_steps_and_chain(self):
        chain = self._approval_chain()
        self._approval_step(chain.id, due_at=utc_now() - timedelta(hours=1))
        result = check_legal_approval_timeouts_task.run()
        self.assertEqual(result["timed_out_steps"], 1)
        self.assertEqual(result["timed_out_chains"], 1)
        self.db.refresh(chain)
        self.assertEqual(chain.status, "timeout")

    def test_approval_timeout_with_pending_sibling_keeps_chain(self):
        chain = self._approval_chain()
        # 步骤1 已到期（由任务标 timeout）；步骤2 未到期（保持 pending）→ 整链不超时
        self._approval_step(chain.id, status="pending", due_at=utc_now() - timedelta(hours=1))
        self._approval_step(chain.id, status="pending", due_at=utc_now() + timedelta(days=1))
        result = check_legal_approval_timeouts_task.run()
        self.assertEqual(result["timed_out_steps"], 1)
        self.assertEqual(result["timed_out_chains"], 0)  # 兄弟步骤未结束，链不超时
        self.db.refresh(chain)
        self.assertEqual(chain.status, "in_progress")


if __name__ == "__main__":
    unittest.main()
