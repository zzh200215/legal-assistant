"""P0 长流程权限快照接入测试：Agent/workflow、合同审查、文书生成。

覆盖：
- 权限变化（普通角色调整）保持快照一致。
- 硬撤销（token_version 失效 / 禁用 / 成员撤销 / 文档授权撤销 / 严格案件成员撤销）
  立即终止：Agent 工具调用被拒绝、合同审查/文书生成不落库。
"""

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import hash_password
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember, LegalMemberRole
from app.models.legal import LegalCase
from app.models.legal_portal import LegalCaseMember
from app.models.document import Document, DocumentAccessRule
from app.models.agent import AgentRun
from app.models.security_auth import AuthorizationSnapshot
from app.services.authorization_service import authorization_service
from app.services.auth_token_service import auth_token_service


class SnapshotFlowBase(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        self.db = self.SessionLocal()
        app.dependency_overrides[get_db] = lambda: self.db

        self.org = Organization(name="律所A", code="ORG_A")
        self.db.add(self.org)
        self.db.flush()
        self.user = User(
            username="lawyer", email="lawyer@test.com",
            hashed_password=hash_password("pw"), status=UserStatus.active.value,
        )
        self.db.add(self.user)
        self.db.flush()
        self.member = OrganizationMember(
            organization_id=self.org.id, user_id=self.user.id, legal_role="editor")
        self.db.add(self.member)
        self.case = LegalCase(
            organization_id=self.org.id, user_id=self.user.id,
            title="案件A", case_type="other", is_strict_mode=0,
        )
        self.db.add(self.case)
        self.db.flush()
        self.db.add(LegalCaseMember(
            case_id=self.case.id, organization_id=self.org.id,
            user_id=self.user.id, case_role="owner", granted_by=self.user.id,
        ))
        self.db.commit()
        self.db.refresh(self.user)
        self.db.refresh(self.case)

    def tearDown(self):
        self.db.close()
        app.dependency_overrides.clear()

    def _capture(self, *, case_id=None, document_id=None):
        ctx = authorization_service.build_context(self.db, self.user, org_id=self.org.id)
        return authorization_service.capture_snapshot(
            self.db, self.user, ctx,
            case_ids=[case_id] if case_id else [],
            document_ids=[document_id] if document_id else [],
        )

    def _assert_flow(self, snapshot_id, *, case_id=None):
        from app.services.legal_workspace_service import legal_workspace_module

        legal_workspace_module._assert_flow_snapshot(self.db, self.user, snapshot_id, case_id=case_id)


class PermissionChangeTests(SnapshotFlowBase):
    """普通权限变化保持快照一致。"""

    def test_role_change_keeps_snapshot(self):
        snap_id = self._capture(case_id=self.case.id)
        # 组织角色从 editor 调整为 reviewer（普通变化）
        self.member.legal_role = LegalMemberRole.reviewer.value
        self.db.commit()
        self._assert_flow(snap_id, case_id=self.case.id)  # 不抛异常

    def test_department_change_keeps_snapshot(self):
        snap_id = self._capture(case_id=self.case.id)
        self.user.department_id = 999
        self.db.commit()
        self._assert_flow(snap_id, case_id=self.case.id)


class HardRevokeSnapshotTests(SnapshotFlowBase):
    """硬撤销立即终止流程。"""

    def test_token_version_bump_terminates(self):
        snap_id = self._capture(case_id=self.case.id)
        auth_token_service.increment_token_version(self.db, self.user)
        with self.assertRaises(Exception) as raised:
            self._assert_flow(snap_id, case_id=self.case.id)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail.get("code"), "TOKEN_VERSION_MISMATCH")

    def test_disabled_user_terminates(self):
        snap_id = self._capture(case_id=self.case.id)
        self.user.status = UserStatus.disabled.value
        self.db.commit()
        with self.assertRaises(Exception) as raised:
            self._assert_flow(snap_id, case_id=self.case.id)
        self.assertEqual(raised.exception.status_code, 403)

    def test_membership_revoked_terminates(self):
        snap_id = self._capture(case_id=self.case.id)
        self.db.delete(self.member)
        self.db.commit()
        with self.assertRaises(Exception) as raised:
            self._assert_flow(snap_id, case_id=self.case.id)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail.get("code"), "MEMBERSHIP_REVOKED")

    def test_document_auth_revoked_terminates(self):
        # 文档 owner 始终可访问，故用他人文档 + 显式分享给当前用户
        other = User(
            username="other", email="other@test.com",
            hashed_password=hash_password("pw"), status=UserStatus.active.value,
        )
        self.db.add(other)
        self.db.flush()
        doc = Document(
            user_id=other.id, organization_id=self.org.id, title="doc",
            file_path="/tmp/x.txt", file_type="txt", permission_scope="restricted",
            download_enabled=True, status="parsed",
        )
        self.db.add(doc)
        self.db.flush()
        self.db.add(DocumentAccessRule(
            document_id=doc.id, subject_type="user",
            subject_value=str(self.user.id), permission="read",
        ))
        self.db.commit()
        snap_id = self._capture(document_id=doc.id)
        # 显式授权被撤销 → 终止
        self.db.query(DocumentAccessRule).filter(
            DocumentAccessRule.document_id == doc.id
        ).delete()
        self.db.commit()
        with self.assertRaises(Exception) as raised:
            self._assert_flow(snap_id)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail.get("code"), "DOCUMENT_AUTH_REVOKED")

    def test_strict_case_membership_revoked_terminates(self):
        self.case.is_strict_mode = 1
        self.db.commit()
        snap_id = self._capture(case_id=self.case.id)
        # 严格案件成员被撤销
        from datetime import datetime, timezone

        self.db.query(LegalCaseMember).filter(LegalCaseMember.case_id == self.case.id).update(
            {"revoked_at": datetime.now(timezone.utc)}
        )
        self.db.commit()
        with self.assertRaises(Exception) as raised:
            self._assert_flow(snap_id, case_id=self.case.id)
        # assert_snapshot 直接抛 CASE_AUTH_REVOKED（403），或 _assert_flow 内部转 LookupError
        exc = raised.exception
        if isinstance(exc, LookupError):
            self.assertEqual(str(exc), "LEGAL_CASE_NOT_FOUND")
        else:
            self.assertEqual(exc.status_code, 403)
            self.assertEqual(exc.detail.get("code"), "CASE_AUTH_REVOKED")


class AgentRunSnapshotTests(SnapshotFlowBase):
    """Agent 工具调用受快照约束。"""

    def _make_run(self):
        run = AgentRun(user_id=self.user.id, goal="测试", status="running")
        self.db.add(run)
        self.db.flush()
        run.authorization_snapshot_id = self._capture(case_id=self.case.id)
        self.db.commit()
        return run

    def test_agent_tool_denied_after_hard_revoke(self):
        import asyncio

        from app.services.agent_service import agent_service

        run = self._make_run()
        self.db.refresh(run)
        # 硬撤销：token_version 递增
        auth_token_service.increment_token_version(self.db, self.user)
        result, _ = asyncio.run(agent_service._execute_tool(
            "doc_read", {"document_id": 1}, self.user.id, self.db,
            agent_run_id=run.id,
        ))
        self.assertFalse(result["success"])
        self.assertEqual(result["mcp_error_code"], "AUTHZ_CHANGED")

    def test_agent_tool_allowed_with_valid_snapshot(self):
        import asyncio

        from app.services.agent_service import agent_service

        run = self._make_run()
        self.db.refresh(run)
        with patch.object(
            agent_service.settings, "AGENT_TOOL_TIMEOUT_SECONDS", 5
        ), patch("app.services.agent_service.mcp_registry") as mock_registry:
            mock_registry.call_tool = AsyncMock(return_value={"success": True, "data": {}})
            result, _ = asyncio.run(agent_service._execute_tool(
                "doc_read", {"document_id": 1}, self.user.id, self.db,
                agent_run_id=run.id,
            ))
        self.assertTrue(result["success"])
        mock_registry.call_tool.assert_awaited_once()

    def test_run_without_snapshot_not_blocked(self):
        import asyncio

        from app.services.agent_service import agent_service

        run = AgentRun(user_id=self.user.id, goal="测试", status="running")
        self.db.add(run)
        self.db.commit()
        with patch("app.services.agent_service.mcp_registry") as mock_registry:
            mock_registry.call_tool = AsyncMock(return_value={"success": True, "data": {}})
            result, _ = asyncio.run(agent_service._execute_tool(
                "doc_read", {"document_id": 1}, self.user.id, self.db,
                agent_run_id=run.id,
            ))
        self.assertTrue(result["success"])


class FlowPersistenceGuardTests(SnapshotFlowBase):
    """合同审查 / 文书生成落库前硬撤销 → 不产生记录。"""

    def _quota_ok(self, *args, **kwargs):
        return True

    def test_contract_review_aborted_on_hard_revoke_before_persist(self):
        import asyncio

        from app.services.legal_workspace_service import legal_workspace_module
        from app.models.legal import ContractReview

        async def revoke_then_review(input_text, user_id=None):
            # 模拟 LLM 调用期间发生硬撤销
            auth_token_service.increment_token_version(self.db, self.user)
            return ([], "ok")

        with patch("app.services.legal_workspace_service.subscription_service.check_quota", self._quota_ok), \
             patch("app.services.legal_workspace_service.subscription_service.ensure_default_plans", lambda *a, **k: None), \
             patch("app.services.legal_workspace_service.review_contract", side_effect=revoke_then_review), \
             patch("app.services.legal_workspace_service.ensure_demo_sources", lambda *a, **k: None):
            with self.assertRaises(Exception):
                asyncio.run(
                    legal_workspace_module.create_contract_review(
                        self.db, self.user, title="合同", content="正文", case_id=self.case.id,
                    )
                )
        # 硬撤销在落库前抛出；不得产生审查记录
        self.assertEqual(
            self.db.query(ContractReview).filter(ContractReview.user_id == self.user.id).count(), 0,
        )

    def test_draft_generation_aborted_on_hard_revoke_before_persist(self):
        import asyncio

        from app.services.legal_workspace_service import legal_workspace_module
        from app.models.legal import LegalDraft

        async def revoke_then_draft(document_type, fields, missing, user_id=None):
            # 模拟生成期间发生硬撤销
            auth_token_service.increment_token_version(self.db, self.user)
            return "生成的文书"

        with patch("app.services.legal_workspace_service.subscription_service.check_quota", self._quota_ok), \
             patch("app.services.legal_workspace_service.subscription_service.ensure_default_plans", lambda *a, **k: None), \
             patch("app.services.legal_workspace_service.draft_content", side_effect=revoke_then_draft), \
             patch("app.services.legal_workspace_service.ensure_demo_sources", lambda *a, **k: None):
            with self.assertRaises(Exception):
                asyncio.run(
                    legal_workspace_module.create_draft(
                        self.db, self.user, document_type="劳动仲裁申请书",
                        fields={"申请人": "张三"}, case_id=self.case.id,
                    )
                )
        # 硬撤销在落库前抛出；不得产生文书草稿
        self.assertEqual(
            self.db.query(LegalDraft).filter(LegalDraft.user_id == self.user.id).count(), 0,
        )


if __name__ == "__main__":
    unittest.main()
