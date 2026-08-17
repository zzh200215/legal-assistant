"""统一授权服务：集中处理用户、组织、部门、案件、文档、客户门户资源权限。

设计要点：
- 所有组织角色从 OrganizationMember.legal_role 实时查询，不信任 JWT 携带的角色。
- 系统 admin（User.role=admin）不得默认跨组织读取租户业务数据：平台权限与租户资源权限分离。
- 角色层级 admin > reviewer > editor > client；层级只用于满足"最低角色"，不能绕过
  组织边界、部门边界、严格案件成员关系、文档显式分享、门户 token 案件范围、用户撤销状态。
- 列表接口用 scope_query(...) 生成 SQLAlchemy 条件；详情接口用 require(...)；二者共用同一规则。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.models.user import User, UserStatus
from app.models.org import OrganizationMember, LegalMemberRole
from app.models.document import Document, DocumentAccessRule, KnowledgeBase
from app.models.legal import LegalCase
from app.models.security_auth import AuthorizationSnapshot


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class PermissionAction(str, Enum):
    """能力清单；每个能力绑定一个最低角色层级。"""

    ORG_READ = "org:read"
    ORG_MANAGE_MEMBERS = "org:manage_members"
    ORG_MANAGE_CONFIG = "org:manage_config"
    ORG_MANAGE_BILLING = "org:manage_billing"

    CASE_READ = "case:read"
    CASE_CREATE = "case:create"
    CASE_UPDATE = "case:update"
    CASE_APPROVE = "case:approve"
    CASE_PUBLISH = "case:publish"
    CASE_MANAGE_MEMBERS = "case:manage_members"
    CASE_MANAGE_RESOURCES = "case:manage_resources"

    DOCUMENT_CREATE = "document:create"
    DOCUMENT_READ = "document:read"
    DOCUMENT_UPDATE = "document:update"
    DOCUMENT_REVIEW = "document:review"
    DOCUMENT_PUBLISH = "document:publish"
    DOCUMENT_DELETE = "document:delete"

    CONTRACT_READ = "contract:read"
    CONTRACT_CREATE = "contract:create"
    INVOICE_READ = "invoice:read"
    INVOICE_MANAGE = "invoice:manage"
    TIME_ENTRY_READ = "time_entry:read"
    TIME_ENTRY_CREATE = "time_entry:create"

    PORTAL_MANAGE = "portal:manage"


_ROLE_LEVEL = {
    LegalMemberRole.admin.value: 4,
    LegalMemberRole.reviewer.value: 3,
    LegalMemberRole.editor.value: 2,
    LegalMemberRole.client.value: 1,
}

# 每个能力的最低角色层级。
_PERMISSION_MIN_LEVEL = {
    PermissionAction.ORG_READ: 1,
    PermissionAction.ORG_MANAGE_MEMBERS: 4,
    PermissionAction.ORG_MANAGE_CONFIG: 4,
    PermissionAction.ORG_MANAGE_BILLING: 4,
    PermissionAction.CASE_READ: 1,
    PermissionAction.CASE_CREATE: 2,
    PermissionAction.CASE_UPDATE: 2,
    PermissionAction.CASE_APPROVE: 3,
    PermissionAction.CASE_PUBLISH: 3,
    PermissionAction.CASE_MANAGE_MEMBERS: 3,
    PermissionAction.CASE_MANAGE_RESOURCES: 2,
    PermissionAction.DOCUMENT_CREATE: 2,
    PermissionAction.DOCUMENT_READ: 1,
    PermissionAction.DOCUMENT_UPDATE: 2,
    PermissionAction.DOCUMENT_REVIEW: 3,
    PermissionAction.DOCUMENT_PUBLISH: 3,
    PermissionAction.DOCUMENT_DELETE: 2,
    PermissionAction.CONTRACT_READ: 1,
    PermissionAction.CONTRACT_CREATE: 2,
    PermissionAction.INVOICE_READ: 1,
    PermissionAction.INVOICE_MANAGE: 4,
    PermissionAction.TIME_ENTRY_READ: 1,
    PermissionAction.TIME_ENTRY_CREATE: 2,
    PermissionAction.PORTAL_MANAGE: 3,
}


def _is_active_case_member(db: Session, case: LegalCase, user_id: int) -> bool:
    from app.models.legal_portal import LegalCaseMember

    return (
        db.query(LegalCaseMember)
        .filter(
            LegalCaseMember.case_id == case.id,
            LegalCaseMember.user_id == user_id,
            LegalCaseMember.revoked_at.is_(None),
        )
        .first()
        is not None
    )


@dataclass
class AuthorizationContext:
    """一次请求/流程的权限上下文（全部来自数据库解析）。"""

    user_id: int
    system_role: str | None = None
    organization_id: int | None = None
    department_id: int | None = None
    member: OrganizationMember | None = None
    legal_role: str | None = None
    token_jti: str | None = None
    token_version: int = 0
    risk_level: str = "low"
    risk_reasons: list[str] = field(default_factory=list)

    @property
    def is_org_member(self) -> bool:
        return self.member is not None

    @property
    def role_level(self) -> int:
        return _ROLE_LEVEL.get(self.legal_role or "", 0)

    @property
    def is_system_admin(self) -> bool:
        return self.system_role == "admin"

    def snapshot_payload(self) -> dict:
        return {
            "user_id": self.user_id,
            "system_role": self.system_role,
            "organization_id": self.organization_id,
            "department_id": self.department_id,
            "legal_role": self.legal_role,
            "token_jti": self.token_jti,
            "token_version": self.token_version,
        }


def _active_user_guard(user: User) -> bool:
    return user.status in (UserStatus.active.value, UserStatus.deletion_pending.value)


class AuthorizationService:
    """统一授权服务单例。"""

    def build_context(
        self,
        db: Session,
        user: User,
        *,
        org_id: int | None = None,
        jti: str | None = None,
        token_version: int | None = None,
    ) -> AuthorizationContext:
        """构建权限上下文：组织成员关系始终实时从 OrganizationMember 查询。"""
        target_org = org_id if org_id is not None else user.organization_id
        member = None
        if target_org:
            member = (
                db.query(OrganizationMember)
                .filter(
                    OrganizationMember.organization_id == target_org,
                    OrganizationMember.user_id == user.id,
                )
                .first()
            )
        return AuthorizationContext(
            user_id=user.id,
            system_role=user.role,
            organization_id=target_org,
            department_id=user.department_id,
            member=member,
            legal_role=member.legal_role if member else None,
            token_jti=jti,
            token_version=token_version if token_version is not None else (user.token_version or 0),
        )

    # ── can / require ────────────────────────────────────────────────────────────

    def can(
        self,
        db: Session,
        ctx: AuthorizationContext,
        action: PermissionAction,
        *,
        case: LegalCase | None = None,
        document: Document | None = None,
        write: bool = False,
    ) -> bool:
        """判断 ctx 是否满足某能力。返回 bool，不抛异常。"""
        if not ctx.is_org_member:
            return False
        min_level = _PERMISSION_MIN_LEVEL.get(action)
        if min_level is None:
            return False
        if ctx.role_level < min_level:
            return False
        if action == PermissionAction.CASE_READ and case is not None:
            return self.can_read_case(db, ctx, case)
        if document is not None and action in (
            PermissionAction.DOCUMENT_READ,
            PermissionAction.DOCUMENT_UPDATE,
        ):
            return self.can_access_document(db, ctx, document, write=write or action == PermissionAction.DOCUMENT_UPDATE)
        return True

    def require(
        self,
        db: Session,
        ctx: AuthorizationContext,
        action: PermissionAction,
        *,
        case: LegalCase | None = None,
        document: Document | None = None,
        write: bool = False,
        hide_404: bool = True,
    ) -> None:
        """不满足时抛异常。跨组织按 403 组织边界处理；资源级隐藏按 404。"""
        if not ctx.is_org_member:
            raise api_error(403, "您不是该组织的成员", code="NOT_ORG_MEMBER")
        min_level = _PERMISSION_MIN_LEVEL.get(action)
        if min_level is None:
            raise api_error(403, "无权执行该操作", code="PERMISSION_DENIED")
        if ctx.role_level < min_level:
            if hide_404:
                raise api_error(404, "资源不存在", code="RESOURCE_NOT_FOUND")
            raise api_error(403, "权限不足", code="INSUFFICIENT_ROLE")
        if action == PermissionAction.CASE_READ and case is not None:
            if not self.can_read_case(db, ctx, case):
                if getattr(case, "is_strict_mode", 0):
                    raise api_error(404, "案件不存在", code="CASE_NOT_FOUND")
                raise api_error(403, "无权访问该案件", code="INSUFFICIENT_ROLE")
        if document is not None and action in (
            PermissionAction.DOCUMENT_READ,
            PermissionAction.DOCUMENT_UPDATE,
        ):
            if not self.can_access_document(
                db, ctx, document, write=write or action == PermissionAction.DOCUMENT_UPDATE
            ):
                raise api_error(404, "文档不存在", code="DOCUMENT_NOT_FOUND")

    # ── 案件 ─────────────────────────────────────────────────────────────────────

    def can_read_case(self, db: Session, ctx: AuthorizationContext, case: LegalCase) -> bool:
        """案件读取规则（列表与详情共用）。

        - 严格案件：仅活跃案件成员可读（LegalCaseMember.revoked_at IS NULL）。
        - 普通案件：admin/reviewer/editor 可读；client 只能读自己创建或活跃成员的案件。
        """
        if not ctx.is_org_member:
            return False
        if getattr(case, "is_strict_mode", 0):
            return _is_active_case_member(db, case, ctx.user_id)
        if ctx.role_level >= 2:
            return True
        if case.user_id == ctx.user_id:
            return True
        return _is_active_case_member(db, case, ctx.user_id)

    def scope_case_query(
        self,
        ctx: AuthorizationContext,
        db: Session,
        org_id: int,
    ):
        """生成 org 下当前用户可见案件列表的 SQLAlchemy 过滤条件。"""
        from app.models.legal_portal import LegalCaseMember

        active_membership = db.query(LegalCaseMember.case_id).filter(
            LegalCaseMember.user_id == ctx.user_id,
            LegalCaseMember.revoked_at.is_(None),
        )
        if ctx.role_level >= 2:
            visible = or_(
                LegalCase.is_strict_mode == 0,
                LegalCase.id.in_(active_membership),
            )
        else:
            # client：自己创建的案件 或 活跃成员案件
            visible = or_(
                LegalCase.user_id == ctx.user_id,
                LegalCase.id.in_(active_membership),
            )
        return and_(LegalCase.organization_id == org_id, visible)

    # ── 文档 ─────────────────────────────────────────────────────────────────────

    def can_access_document(
        self,
        db: Session,
        ctx: AuthorizationContext,
        document: Document,
        *,
        write: bool = False,
    ) -> bool:
        """文档读取/写入规则（与 SQL scope 过滤共用同一语义）。

        private：创建者；department：同组织同部门；organization/org：同组织成员；
        public：仅认证用户且默认限制在同组织（禁止默认跨租户公开）；
        restricted/role/user：显式授权规则（DocumentAccessRule）。
        """
        if document.user_id == ctx.user_id:
            return True

        scope = (document.permission_scope or "private").strip().lower()

        if scope in ("organization", "org"):
            return bool(
                ctx.organization_id
                and document.organization_id
                and document.organization_id == ctx.organization_id
            )
        if scope == "department":
            return bool(
                ctx.department_id
                and document.department_id
                and document.department_id == ctx.department_id
            )
        if scope == "public":
            # 默认限制在同组织，禁止默认跨租户公开。
            return bool(
                ctx.organization_id
                and document.organization_id
                and document.organization_id == ctx.organization_id
            )
        if scope in ("restricted", "role", "private"):
            return self._explicit_rule_allows(db, ctx, document, write=write)
        return False

    def can_access_knowledge_base(
        self,
        db: Session,
        ctx: AuthorizationContext,
        kb: KnowledgeBase,
    ) -> bool:
        if kb.user_id == ctx.user_id:
            return True
        scope = (kb.permission_scope or "private").strip().lower()
        if scope in ("organization", "org"):
            return bool(
                ctx.organization_id and kb.organization_id and kb.organization_id == ctx.organization_id
            )
        if scope == "department":
            return bool(
                ctx.department_id and kb.department_id and kb.department_id == ctx.department_id
            )
        if scope == "public":
            return bool(
                ctx.organization_id and kb.organization_id and kb.organization_id == ctx.organization_id
            )
        return False

    def _explicit_rule_allows(
        self,
        db: Session,
        ctx: AuthorizationContext,
        document: Document,
        *,
        write: bool,
    ) -> bool:
        rules = (
            db.query(DocumentAccessRule)
            .filter(DocumentAccessRule.document_id == document.id)
            .all()
        )
        for rule in rules:
            if write and rule.permission != "write":
                continue
            if rule.subject_type == "user" and rule.subject_value == str(ctx.user_id):
                return True
            if rule.subject_type == "role" and rule.subject_value in (
                ctx.legal_role,
                ctx.system_role,
            ):
                return True
            if rule.subject_type == "department" and rule.subject_value == str(ctx.department_id):
                return True
            if rule.subject_type == "organization" and rule.subject_value == str(ctx.organization_id):
                return True
        return False

    def document_scope_filter(
        self,
        db: Session,
        *,
        user_id: int,
        organization_id: int | None,
        department_id: int | None,
        role: str | None,
    ):
        """生成当前用户可见文档的 SQLAlchemy 条件（列表查询用，不读全表）。"""
        conds = [Document.user_id == user_id]
        if organization_id:
            conds.append(
                and_(
                    Document.permission_scope.in_(("organization", "org")),
                    Document.organization_id == organization_id,
                )
            )
            # public 默认限制在同组织，禁止默认跨租户公开。
            conds.append(
                and_(
                    Document.permission_scope == "public",
                    Document.organization_id == organization_id,
                )
            )
        if department_id:
            conds.append(
                and_(
                    Document.permission_scope == "department",
                    Document.department_id == department_id,
                )
            )
        rule_doc_ids = db.query(DocumentAccessRule.document_id).filter(
            or_(
                and_(
                    DocumentAccessRule.subject_type == "user",
                    DocumentAccessRule.subject_value == str(user_id),
                ),
                and_(
                    DocumentAccessRule.subject_type == "role",
                    DocumentAccessRule.subject_value == role,
                ),
                and_(
                    DocumentAccessRule.subject_type == "department",
                    DocumentAccessRule.subject_value == str(department_id),
                ),
                and_(
                    DocumentAccessRule.subject_type == "organization",
                    DocumentAccessRule.subject_value == str(organization_id),
                ),
            )
        )
        conds.append(Document.id.in_(rule_doc_ids))
        return or_(*conds)

    def knowledge_base_scope_filter(
        self,
        *,
        user_id: int,
        organization_id: int | None,
        department_id: int | None,
    ):
        conds = [KnowledgeBase.user_id == user_id]
        if organization_id:
            conds.append(
                and_(
                    KnowledgeBase.permission_scope == "organization",
                    KnowledgeBase.organization_id == organization_id,
                )
            )
            conds.append(
                and_(
                    KnowledgeBase.permission_scope == "public",
                    KnowledgeBase.organization_id == organization_id,
                )
            )
        if department_id:
            conds.append(
                and_(
                    KnowledgeBase.permission_scope == "department",
                    KnowledgeBase.department_id == department_id,
                )
            )
        return or_(*conds)

    def scope_query(
        self,
        db: Session,
        model,
        ctx: AuthorizationContext,
        action: PermissionAction,
        *,
        org_id: int | None = None,
    ):
        """列表统一入口：返回带权限过滤的 SQLAlchemy query。"""
        if action == PermissionAction.CASE_READ:
            return db.query(model).filter(self.scope_case_query(ctx, db, org_id))
        if model is Document and action == PermissionAction.DOCUMENT_READ:
            return db.query(model).filter(
                self.document_scope_filter(
                    db,
                    user_id=ctx.user_id,
                    organization_id=ctx.organization_id,
                    department_id=ctx.department_id,
                    role=ctx.legal_role or ctx.system_role,
                )
            )
        if model is KnowledgeBase and action == PermissionAction.DOCUMENT_READ:
            return db.query(model).filter(
                self.knowledge_base_scope_filter(
                    user_id=ctx.user_id,
                    organization_id=ctx.organization_id,
                    department_id=ctx.department_id,
                )
            )
        return db.query(model)

    # ── 长流程权限快照 ───────────────────────────────────────────────────────────

    def capture_snapshot(
        self,
        db: Session,
        user: User,
        ctx: AuthorizationContext,
        *,
        case_ids: Optional[list[int]] = None,
        document_ids: Optional[list[int]] = None,
        resource_ids: Optional[list[int]] = None,
        explicit_shares: Optional[dict] = None,
        expires_minutes: int = 120,
    ) -> str:
        """创建权限快照，返回 snapshot_id（客户端只允许提交该 ID）。"""
        payload = ctx.snapshot_payload()
        payload.update(
            {
                "case_ids": sorted(set(case_ids or [])),
                "document_ids": sorted(set(document_ids or [])),
                "resource_ids": sorted(set(resource_ids or [])),
                "explicit_shares": explicit_shares or {},
            }
        )
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        snapshot_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        snapshot_id = secrets.token_urlsafe(16)
        db.add(
            AuthorizationSnapshot(
                snapshot_id=snapshot_id,
                user_id=user.id,
                organization_id=ctx.organization_id,
                department_id=ctx.department_id,
                legal_role=ctx.legal_role,
                token_version=ctx.token_version,
                jti=ctx.token_jti,
                resource_scope_json=json.dumps(
                    {
                        "case_ids": payload["case_ids"],
                        "document_ids": payload["document_ids"],
                        "resource_ids": payload["resource_ids"],
                    },
                    ensure_ascii=False,
                ),
                explicit_shares_json=json.dumps(explicit_shares or {}, ensure_ascii=False),
                policy_version=1,
                snapshot_hash=snapshot_hash,
                expires_at=utc_now() + timedelta(minutes=expires_minutes),
            )
        )
        db.commit()
        return snapshot_id

    def assert_snapshot(self, db: Session, snapshot_id: str, *, user_id: int) -> AuthorizationSnapshot:
        """校验快照可用：未过期、未撤销、token_version 仍匹配、用户仍 active。

        用户被禁用/删除/强制退出或 token_version 失效时，快照立即终止。
        若快照记录了组织/文档范围，还会重新校验组织成员关系与文档访问，
        使"组织成员被撤销 / 文档显式授权被撤销"等硬撤销立即终止流程。
        """
        snapshot = (
            db.query(AuthorizationSnapshot)
            .filter(AuthorizationSnapshot.snapshot_id == snapshot_id)
            .first()
        )
        if not snapshot:
            raise api_error(403, "授权快照无效", code="SNAPSHOT_INVALID")
        if snapshot.user_id != user_id:
            raise api_error(403, "授权快照无效", code="SNAPSHOT_INVALID")
        if snapshot.revoked_at is not None:
            raise api_error(403, "授权快照已撤销", code="SNAPSHOT_REVOKED")
        expires_at = _coerce_utc(snapshot.expires_at)
        if expires_at and expires_at < utc_now():
            raise api_error(403, "授权快照已过期", code="SNAPSHOT_EXPIRED")
        user = db.query(User).filter(User.id == user_id).first()
        if not user or not _active_user_guard(user):
            raise api_error(403, "账号状态异常", code="USER_DISABLED")
        if (user.token_version or 0) != snapshot.token_version:
            raise api_error(403, "登录态已失效", code="TOKEN_VERSION_MISMATCH")

        # 硬撤销：组织成员关系被撤销 → 立即终止。
        if snapshot.organization_id:
            member = (
                db.query(OrganizationMember)
                .filter(
                    OrganizationMember.organization_id == snapshot.organization_id,
                    OrganizationMember.user_id == user_id,
                )
                .first()
            )
            if not member:
                raise api_error(403, "组织成员关系已失效", code="MEMBERSHIP_REVOKED")

        # 硬撤销：文档显式授权被撤销 → 立即终止。
        if snapshot.resource_scope_json:
            try:
                scope = json.loads(snapshot.resource_scope_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                scope = {}
            document_ids = scope.get("document_ids") or []
            if document_ids:
                ctx = AuthorizationContext(
                    user_id=user_id,
                    system_role=user.role,
                    organization_id=snapshot.organization_id,
                    department_id=snapshot.department_id,
                    legal_role=snapshot.legal_role,
                )
                for doc_id in document_ids:
                    doc = db.query(Document).filter(Document.id == doc_id).first()
                    if not doc or not self.can_access_document(db, ctx, doc):
                        raise api_error(403, "文档授权已失效", code="DOCUMENT_AUTH_REVOKED")
            # 硬撤销：严格案件成员关系被撤销 → 立即终止。
            case_ids = scope.get("case_ids") or []
            for case_id in case_ids:
                case = db.query(LegalCase).filter(LegalCase.id == case_id).first()
                if not case:
                    raise api_error(403, "案件已不存在", code="CASE_AUTH_REVOKED")
                if getattr(case, "is_strict_mode", 0) and not _is_active_case_member(
                    db, case, user_id
                ):
                    raise api_error(403, "案件成员关系已撤销", code="CASE_AUTH_REVOKED")
        return snapshot

    def revoke_snapshot(self, db: Session, snapshot_id: str, *, reason: str = "revoked") -> None:
        snapshot = (
            db.query(AuthorizationSnapshot)
            .filter(AuthorizationSnapshot.snapshot_id == snapshot_id)
            .first()
        )
        if snapshot and snapshot.revoked_at is None:
            snapshot.revoked_at = utc_now()
            snapshot.revoke_reason = reason
            db.add(snapshot)
            db.commit()


authorization_service = AuthorizationService()
