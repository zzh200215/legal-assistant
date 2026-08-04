"""
授权系统全面测试 — P0-01 任务

测试场景覆盖：
1. 未登录访问
2. 跨组织访问（用户A访问组织B的资源）
3. 越权角色（client角色尝试执行admin操作）
4. 已移除案件成员尝试访问
5. 严格模式案件权限
6. IDOR漏洞测试

测试目标：
- 组织成员检查：require_org_member
- 角色权限检查：require_org_role
- 案件访问权限：require_case_access
- 资源级权限：合同、账单、计时记录
"""

import unittest
from datetime import datetime, timezone
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.auth import (
    create_access_token,
    hash_password,
    require_case_access,
    require_resource_scope,
    verify_resource_access,
)
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User, UserStatus
from app.models.org import Organization, OrganizationMember, LegalMemberRole
from app.models.legal import LegalCase
from app.models.legal_contract import LegalContract
from app.models.legal_billing import LegalTimeEntry, LegalInvoice
from app.models.legal_portal import LegalCaseMember


class AuthorizationTests(unittest.TestCase):
    """授权系统测试套件"""

    def setUp(self):
        """测试环境初始化"""
        # 创建内存数据库
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()

        # 覆盖依赖
        app.dependency_overrides[get_db] = lambda: self.db
        self.client = TestClient(app)

        # 创建测试数据
        self._create_test_data()

    def tearDown(self):
        """清理测试环境"""
        self.db.close()
        app.dependency_overrides.clear()

    def _create_test_data(self):
        """创建测试数据：2个组织，多个用户，多个角色"""

        # ── 组织A ──
        self.org_a = Organization(
            name="律所A",
            code="ORG_A",
            description="测试律所A"
        )
        self.db.add(self.org_a)
        self.db.flush()

        # ── 组织B ──
        self.org_b = Organization(
            name="律所B",
            code="ORG_B",
            description="测试律所B"
        )
        self.db.add(self.org_b)
        self.db.flush()

        # ── 用户创建 ──
        # 组织A的管理员
        self.user_a_admin = User(
            username="user_a_admin",
            email="admin_a@test.com",
            hashed_password=hash_password("password123"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.user_a_admin)
        self.db.flush()

        # 组织A的编辑
        self.user_a_editor = User(
            username="user_a_editor",
            email="editor_a@test.com",
            hashed_password=hash_password("password123"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.user_a_editor)
        self.db.flush()

        # 组织A的客户
        self.user_a_client = User(
            username="user_a_client",
            email="client_a@test.com",
            hashed_password=hash_password("password123"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.user_a_client)
        self.db.flush()

        # 组织B的管理员
        self.user_b_admin = User(
            username="user_b_admin",
            email="admin_b@test.com",
            hashed_password=hash_password("password123"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.user_b_admin)
        self.db.flush()

        # 无组织用户
        self.user_no_org = User(
            username="user_no_org",
            email="noorg@test.com",
            hashed_password=hash_password("password123"),
            role="user",
            status=UserStatus.active.value,
        )
        self.db.add(self.user_no_org)
        self.db.flush()

        # ── 组织成员关系 ──
        self.member_a_admin = OrganizationMember(
            organization_id=self.org_a.id,
            user_id=self.user_a_admin.id,
            legal_role=LegalMemberRole.admin.value,
            joined_at=datetime.now(timezone.utc),
        )
        self.db.add(self.member_a_admin)

        self.member_a_editor = OrganizationMember(
            organization_id=self.org_a.id,
            user_id=self.user_a_editor.id,
            legal_role=LegalMemberRole.editor.value,
            joined_at=datetime.now(timezone.utc),
        )
        self.db.add(self.member_a_editor)

        self.member_a_client = OrganizationMember(
            organization_id=self.org_a.id,
            user_id=self.user_a_client.id,
            legal_role=LegalMemberRole.client.value,
            joined_at=datetime.now(timezone.utc),
        )
        self.db.add(self.member_a_client)

        self.member_b_admin = OrganizationMember(
            organization_id=self.org_b.id,
            user_id=self.user_b_admin.id,
            legal_role=LegalMemberRole.admin.value,
            joined_at=datetime.now(timezone.utc),
        )
        self.db.add(self.member_b_admin)

        self.db.commit()

        # ── 案件数据 ──
        self.case_a = LegalCase(
            organization_id=self.org_a.id,
            user_id=self.user_a_admin.id,
            title="案件A - 劳动争议",
            case_type="labor_dispute",
            status="in_progress",
        )
        self.db.add(self.case_a)
        self.db.flush()

        self.case_b = LegalCase(
            organization_id=self.org_b.id,
            user_id=self.user_b_admin.id,
            title="案件B - 合同纠纷",
            case_type="contract_dispute",
            status="in_progress",
        )
        self.db.add(self.case_b)
        self.db.flush()

        # ── 合同数据 ──
        self.contract_a = LegalContract(
            organization_id=self.org_a.id,
            case_id=self.case_a.id,
            contract_no="CON-A-001",
            title="组织A合同",
            status="active",
            created_by=self.user_a_admin.id,
        )
        self.db.add(self.contract_a)
        self.db.flush()

        self.contract_b = LegalContract(
            organization_id=self.org_b.id,
            case_id=self.case_b.id,
            contract_no="CON-B-001",
            title="组织B合同",
            status="active",
            created_by=self.user_b_admin.id,
        )
        self.db.add(self.contract_b)
        self.db.flush()

        # ── 计时记录 ──
        self.time_entry_a = LegalTimeEntry(
            organization_id=self.org_a.id,
            case_id=self.case_a.id,
            operator_id=self.user_a_editor.id,
            description="案件工作",
            status="completed",
            duration_minutes=120,
        )
        self.db.add(self.time_entry_a)
        self.db.flush()

        self.time_entry_b = LegalTimeEntry(
            organization_id=self.org_b.id,
            case_id=self.case_b.id,
            operator_id=self.user_b_admin.id,
            description="案件工作",
            status="completed",
            duration_minutes=90,
        )
        self.db.add(self.time_entry_b)
        self.db.flush()

        # ── 发票数据 ──
        self.invoice_a = LegalInvoice(
            organization_id=self.org_a.id,
            case_id=self.case_a.id,
            invoice_no="INV-A-001",
            client_display_name="客户A",
            issue_date=datetime.now(timezone.utc).date(),
            subtotal=1000.00,
            total_amount=1000.00,
            status="draft",
            created_by=self.user_a_admin.id,
        )
        self.db.add(self.invoice_a)
        self.db.flush()

        self.invoice_b = LegalInvoice(
            organization_id=self.org_b.id,
            case_id=self.case_b.id,
            invoice_no="INV-B-001",
            client_display_name="客户B",
            issue_date=datetime.now(timezone.utc).date(),
            subtotal=2000.00,
            total_amount=2000.00,
            status="draft",
            created_by=self.user_b_admin.id,
        )
        self.db.add(self.invoice_b)

        self.db.commit()

        # ── 生成认证token ──
        self.token_a_admin = create_access_token({"sub": self.user_a_admin.id, "role": "user"})
        self.token_a_editor = create_access_token({"sub": self.user_a_editor.id, "role": "user"})
        self.token_a_client = create_access_token({"sub": self.user_a_client.id, "role": "user"})
        self.token_b_admin = create_access_token({"sub": self.user_b_admin.id, "role": "user"})
        self.token_no_org = create_access_token({"sub": self.user_no_org.id, "role": "user"})


    # ──────────────────────────────────────────────────────────────────────────────
    # 1. 未登录访问测试
    # ──────────────────────────────────────────────────────────────────────────────

    def test_unauthenticated_access_list_cases(self):
        """未登录访问案件列表应返回401"""
        resp = self.client.get(f"/api/legal/orgs/{self.org_a.id}/cases")
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_access_create_contract(self):
        """未登录创建合同应返回401"""
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_a.id}/contracts",
            json={"title": "测试合同", "contract_no": "TEST-001"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_unauthenticated_access_time_entry(self):
        """未登录访问计时记录应返回401"""
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_a.id}/cases/{self.case_a.id}/time-entries",
            json={"description": "工作", "duration_minutes": 60}
        )
        self.assertEqual(resp.status_code, 401)


    # ──────────────────────────────────────────────────────────────────────────────
    # 2. 跨组织访问测试（IDOR漏洞防护）
    # ──────────────────────────────────────────────────────────────────────────────

    def test_cross_org_access_case(self):
        """组织B的用户无法访问组织A的案件（非组织成员统一拒绝）"""
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases/{self.case_a.id}",
            headers={"Authorization": f"Bearer {self.token_b_admin}"}
        )
        # org_id 显式来自URL，非该组织成员直接拒绝（403），不涉及案件存在性泄露
        self.assertEqual(resp.status_code, 403)

    def test_cross_org_access_contract(self):
        """组织B的用户无法访问组织A的合同"""
        resp = self.client.get(
            f"/api/legal/contracts/{self.contract_a.id}",
            headers={"Authorization": f"Bearer {self.token_b_admin}"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_cross_org_create_contract(self):
        """组织B的用户无法在组织A创建合同"""
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_a.id}/contracts",
            headers={"Authorization": f"Bearer {self.token_b_admin}"},
            json={"title": "跨组织合同", "contract_no": "CROSS-001"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_cross_org_access_time_entry(self):
        """组织B的用户无法访问组织A的计时记录"""
        # 注意：根据实际API路径调整
        # 如果API是 /api/time-entries/{id}，测试直接访问
        pass  # 需要根据实际API实现

    def test_cross_org_access_invoice(self):
        """组织B的用户无法访问组织A的发票"""
        # 如果有GET /api/invoices/{id} 端点
        pass  # 需要根据实际API实现

    def test_no_org_user_cannot_access(self):
        """无组织用户无法访问任何组织资源"""
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": f"Bearer {self.token_no_org}"}
        )
        self.assertEqual(resp.status_code, 403)


    # ──────────────────────────────────────────────────────────────────────────────
    # 3. 角色权限测试
    # ──────────────────────────────────────────────────────────────────────────────

    def test_client_cannot_create_contract(self):
        """client角色无法创建合同（需要editor权限）"""
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_a.id}/contracts",
            headers={"Authorization": f"Bearer {self.token_a_client}"},
            json={"title": "客户创建的合同", "contract_no": "CLIENT-001"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_client_cannot_create_case(self):
        """client角色无法创建案件（需要editor权限）"""
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": f"Bearer {self.token_a_client}"},
            json={
                "title": "客户创建的案件",
                "case_type": "other",
                "organization_id": self.org_a.id
            }
        )
        self.assertEqual(resp.status_code, 403)

    def test_editor_can_create_contract(self):
        """editor角色可以创建合同"""
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_a.id}/contracts",
            headers={"Authorization": f"Bearer {self.token_a_editor}"},
            json={"title": "编辑创建的合同", "contract_no": "EDITOR-001"}
        )
        self.assertIn(resp.status_code, [200, 201])

    def test_admin_can_create_contract(self):
        """admin角色可以创建合同"""
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_a.id}/contracts",
            headers={"Authorization": f"Bearer {self.token_a_admin}"},
            json={"title": "管理员创建的合同", "contract_no": "ADMIN-001"}
        )
        self.assertIn(resp.status_code, [200, 201])

    def test_client_can_read_own_org_resources(self):
        """client角色可以读取本组织资源（如果API允许）"""
        # 测试案件列表访问
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": f"Bearer {self.token_a_client}"}
        )
        # client可能可以查看，取决于业务逻辑
        self.assertIn(resp.status_code, [200, 403])


    # ──────────────────────────────────────────────────────────────────────────────
    # 4. 案件成员权限测试（严格模式）
    # ──────────────────────────────────────────────────────────────────────────────

    def test_case_member_access_with_strict_mode(self):
        """严格模式：只有案件成员可以访问"""
        self.case_a.is_strict_mode = 1
        # 创建案件成员关系
        case_member = LegalCaseMember(
            case_id=self.case_a.id,
            organization_id=self.org_a.id,
            user_id=self.user_a_editor.id,
            case_role="collaborator",
            granted_by=self.user_a_admin.id,
        )
        self.db.add(case_member)
        self.db.commit()

        # 案件成员可以访问
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases/{self.case_a.id}",
            headers={"Authorization": f"Bearer {self.token_a_editor}"}
        )
        self.assertEqual(resp.status_code, 200)

    def test_non_case_member_cannot_access_strict_case(self):
        """非案件成员无法访问严格模式案件"""
        self.case_a.is_strict_mode = 1
        self.db.commit()
        # user_a_client 不是案件成员
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases/{self.case_a.id}",
            headers={"Authorization": f"Bearer {self.token_a_client}"}
        )
        # 返回404避免泄露资源存在性
        self.assertEqual(resp.status_code, 404)

    def test_revoked_case_member_cannot_access(self):
        """已移除的案件成员无法访问"""
        self.case_a.is_strict_mode = 1
        # 创建并移除案件成员
        case_member = LegalCaseMember(
            case_id=self.case_a.id,
            organization_id=self.org_a.id,
            user_id=self.user_a_editor.id,
            case_role="collaborator",
            granted_by=self.user_a_admin.id,
            revoked_at=datetime.now(timezone.utc),  # 已移除
        )
        self.db.add(case_member)
        self.db.commit()

        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases/{self.case_a.id}",
            headers={"Authorization": f"Bearer {self.token_a_editor}"}
        )
        # 已移除的成员应该无法访问
        self.assertIn(resp.status_code, [403, 404])

    def test_dependency_case_access_rejects_revoked_strict_member(self):
        """依赖式案件校验与手动校验必须同样拒绝已撤销成员。"""
        self.case_a.is_strict_mode = 1
        self.db.add(LegalCaseMember(
            case_id=self.case_a.id,
            organization_id=self.org_a.id,
            user_id=self.user_a_editor.id,
            case_role="collaborator",
            granted_by=self.user_a_admin.id,
            revoked_at=datetime.now(timezone.utc),
        ))
        self.db.commit()

        with self.assertRaises(HTTPException) as raised:
            require_case_access(self.case_a.id)(self.user_a_editor, self.db)
        self.assertEqual(raised.exception.status_code, 404)

    def test_resource_scope_dependency_reads_path_parameter(self):
        """通用资源依赖从路径参数解析 ID，拒绝跨组织直接枚举。"""
        request = type("RequestStub", (), {"path_params": {"contract_id": str(self.contract_a.id)}})()
        scope = require_resource_scope("contract", "contract_id")
        with self.assertRaises(HTTPException) as raised:
            scope(self.user_b_admin, self.db, request)
        self.assertEqual(raised.exception.status_code, 404)


    # ──────────────────────────────────────────────────────────────────────────────
    # 5. IDOR漏洞测试（通过修改参数访问其他资源）
    # ──────────────────────────────────────────────────────────────────────────────

    def test_idor_modify_org_id_in_path(self):
        """尝试通过修改URL中的org_id访问其他组织"""
        # 用户B尝试访问组织A的合同
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/contracts",
            headers={"Authorization": f"Bearer {self.token_b_admin}"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_idor_modify_case_id_in_time_entry(self):
        """尝试为其他组织的案件创建计时记录"""
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_a.id}/cases/{self.case_a.id}/time-entries",
            headers={"Authorization": f"Bearer {self.token_b_admin}"},
            json={
                "case_id": self.case_a.id,  # 尝试访问组织A的案件
                "description": "跨组织计时",
                "duration_minutes": 60
            }
        )
        # 案件级校验统一返回404，避免泄露案件存在性
        self.assertEqual(resp.status_code, 404)

    def test_idor_access_resource_by_direct_id(self):
        """直接通过资源ID访问时应验证组织归属"""
        # 如果API支持 GET /api/contracts/{id}
        # 组织B用户访问组织A的合同ID
        resp = self.client.get(
            f"/api/legal/contracts/{self.contract_a.id}",
            headers={"Authorization": f"Bearer {self.token_b_admin}"}
        )
        self.assertEqual(resp.status_code, 404)  # 返回404而非403

    def test_cross_org_time_entry_and_invoice_are_hidden(self):
        """资源级校验对跨组织计时记录、发票统一返回404。"""
        for resource_type, resource_id in (
            ("time_entry", self.time_entry_a.id),
            ("invoice", self.invoice_a.id),
        ):
            with self.subTest(resource_type=resource_type):
                with self.assertRaises(HTTPException) as raised:
                    verify_resource_access(resource_type, resource_id, self.user_b_admin.id, self.db)
                self.assertEqual(raised.exception.status_code, 404)

    def test_resource_editor_requirement_is_hidden(self):
        """同组织 client 不可通过合同资源执行 editor 级操作。"""
        with self.assertRaises(HTTPException) as raised:
            verify_resource_access(
                "contract",
                self.contract_a.id,
                self.user_a_client.id,
                self.db,
                min_role=LegalMemberRole.editor,
            )
        self.assertEqual(raised.exception.status_code, 404)


    # ──────────────────────────────────────────────────────────────────────────────
    # 6. 资源级权限集成测试
    # ──────────────────────────────────────────────────────────────────────────────

    def test_contract_access_same_org_different_roles(self):
        """同组织不同角色访问合同"""
        # Admin可以访问
        resp_admin = self.client.get(
            f"/api/legal/contracts/{self.contract_a.id}",
            headers={"Authorization": f"Bearer {self.token_a_admin}"}
        )
        self.assertEqual(resp_admin.status_code, 200)

        # Editor可以访问
        resp_editor = self.client.get(
            f"/api/legal/contracts/{self.contract_a.id}",
            headers={"Authorization": f"Bearer {self.token_a_editor}"}
        )
        self.assertEqual(resp_editor.status_code, 200)

        # Client可能可以访问（取决于业务逻辑）
        resp_client = self.client.get(
            f"/api/legal/contracts/{self.contract_a.id}",
            headers={"Authorization": f"Bearer {self.token_a_client}"}
        )
        self.assertIn(resp_client.status_code, [200, 403])

    def test_invoice_creation_requires_admin(self):
        """创建发票需要admin权限"""
        # Editor尝试创建发票（如果需要admin权限）
        # 注意：根据实际业务逻辑，editor可能可以创建发票
        pass  # 需要根据实际API实现

    def test_time_entry_operator_verification(self):
        """计时记录的操作人验证"""
        # 用户只能为自己创建计时记录，或admin可以为其他人创建
        resp = self.client.post(
            f"/api/legal/orgs/{self.org_a.id}/cases/{self.case_a.id}/time-entries",
            headers={"Authorization": f"Bearer {self.token_a_editor}"},
            json={
                "case_id": self.case_a.id,
                "description": "正常计时",
                "duration_minutes": 60
            }
        )
        self.assertIn(resp.status_code, [200, 201])


    # ──────────────────────────────────────────────────────────────────────────────
    # 7. 边界情况测试
    # ──────────────────────────────────────────────────────────────────────────────

    def test_deleted_user_cannot_access(self):
        """已删除用户无法访问"""
        # 禁用用户
        self.user_a_editor.status = UserStatus.disabled.value
        self.db.commit()

        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": f"Bearer {self.token_a_editor}"}
        )
        # 取决于实现，可能返回401或403
        self.assertIn(resp.status_code, [401, 403])

        # 恢复状态
        self.user_a_editor.status = UserStatus.active.value
        self.db.commit()

    def test_invalid_token_access(self):
        """无效token无法访问"""
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": "Bearer invalid_token_12345"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_expired_token_access(self):
        """过期token无法访问"""
        # 创建一个已过期的token
        from datetime import timedelta
        expired_token = create_access_token(
            {"sub": self.user_a_admin.id, "role": "user"},
            expires_delta=timedelta(seconds=-1)  # 已过期
        )
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": f"Bearer {expired_token}"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_malformed_authorization_header(self):
        """错误格式的Authorization头"""
        resp = self.client.get(
            f"/api/legal/orgs/{self.org_a.id}/cases",
            headers={"Authorization": "InvalidFormat"}
        )
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
