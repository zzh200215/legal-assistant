"""法律业务组织成员管理 API — Phase 9 Week 1

管理律所/企业法务内的成员及法律专用角色（Admin/Reviewer/Editor/Client）。
与系统级 User.role 独立：同一个系统用户可以在某律所组织里担任 Reviewer。
"""
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.org import Organization, OrganizationMember, LegalMemberRole
from app.models.user import User

router = APIRouter()

VALID_ROLES = {r.value for r in LegalMemberRole}


# ── Schemas ───────────────────────────────────────────────────────────────────

class MemberInviteIn(BaseModel):
    user_id: int
    legal_role: str = Field(default=LegalMemberRole.client.value)


class MemberRoleUpdate(BaseModel):
    legal_role: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_admin(db: Session, user_id: int, org_id: int) -> OrganizationMember:
    """验证用户是该组织的 admin 角色。"""
    member = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.user_id == user_id,
    ).first()
    if not member or member.legal_role != LegalMemberRole.admin.value:
        raise api_error(403, "需要组织管理员权限", code="ORG_ADMIN_REQUIRED")
    return member


def _serialize_member(m: OrganizationMember, db: Session) -> dict:
    user = db.get(User, m.user_id)
    return {
        "id": m.id,
        "organization_id": m.organization_id,
        "user_id": m.user_id,
        "username": user.username if user else None,
        "full_name": user.full_name if user else None,
        "email": user.email if user else None,
        "legal_role": m.legal_role,
        "joined_at": m.joined_at,
        "created_at": m.created_at,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/orgs/{org_id}/members")
def list_members(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出组织成员（需要是组织成员）。"""
    org = db.get(Organization, org_id)
    if not org:
        raise api_error(404, "组织不存在", code="ORG_NOT_FOUND")
    # 验证调用者是成员
    caller = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.user_id == current_user.id,
    ).first()
    if not caller:
        raise api_error(403, "不是该组织成员", code="NOT_ORG_MEMBER")
    members = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id
    ).all()
    return [_serialize_member(m, db) for m in members]


@router.post("/orgs/{org_id}/members", status_code=201)
def invite_member(
    org_id: int,
    req: MemberInviteIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """邀请用户加入组织（需要 admin 角色）。"""
    _require_admin(db, current_user.id, org_id)
    if req.legal_role not in VALID_ROLES:
        raise api_error(400, f"无效的角色，支持: {VALID_ROLES}", code="INVALID_ROLE")
    existing = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.user_id == req.user_id,
    ).first()
    if existing:
        raise api_error(409, "该用户已是组织成员", code="MEMBER_EXISTS")
    user = db.get(User, req.user_id)
    if not user:
        raise api_error(404, "用户不存在", code="USER_NOT_FOUND")
    member = OrganizationMember(
        organization_id=org_id,
        user_id=req.user_id,
        legal_role=req.legal_role,
        invited_by=current_user.id,
        invite_token=secrets.token_urlsafe(32),
        joined_at=datetime.now(timezone.utc),
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return _serialize_member(member, db)


@router.patch("/orgs/{org_id}/members/{member_id}")
def update_member_role(
    org_id: int,
    member_id: int,
    req: MemberRoleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """修改成员角色（需要 admin 角色，且不能降低自己的权限）。"""
    _require_admin(db, current_user.id, org_id)
    if req.legal_role not in VALID_ROLES:
        raise api_error(400, f"无效的角色，支持: {VALID_ROLES}", code="INVALID_ROLE")
    member = db.query(OrganizationMember).filter(
        OrganizationMember.id == member_id,
        OrganizationMember.organization_id == org_id,
    ).first()
    if not member:
        raise api_error(404, "成员不存在", code="MEMBER_NOT_FOUND")
    if member.user_id == current_user.id and req.legal_role != LegalMemberRole.admin.value:
        raise api_error(400, "不能修改自己的 admin 权限", code="SELF_DEMOTE")
    member.legal_role = req.legal_role
    db.commit()
    db.refresh(member)
    return _serialize_member(member, db)


@router.delete("/orgs/{org_id}/members/{member_id}", status_code=204)
def remove_member(
    org_id: int,
    member_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """移除成员（admin 操作，不能移除自己）。"""
    _require_admin(db, current_user.id, org_id)
    member = db.query(OrganizationMember).filter(
        OrganizationMember.id == member_id,
        OrganizationMember.organization_id == org_id,
    ).first()
    if not member:
        raise api_error(404, "成员不存在", code="MEMBER_NOT_FOUND")
    if member.user_id == current_user.id:
        raise api_error(400, "不能移除自己", code="SELF_REMOVE")
    db.delete(member)
    db.commit()


@router.post("/accept-invite")
def accept_invite(
    invite_token: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """通过 invite_token 接受组织邀请。"""
    member = db.query(OrganizationMember).filter(
        OrganizationMember.invite_token == invite_token,
    ).first()
    if not member:
        raise api_error(400, "邀请 token 无效", code="INVALID_INVITE_TOKEN")
    if member.joined_at is not None:
        raise api_error(400, "邀请已被使用", code="INVITE_ALREADY_ACCEPTED")
    member.joined_at = datetime.now(timezone.utc)
    member.invite_token = None
    current_user.organization_id = member.organization_id
    db.commit()
    db.refresh(member)
    return {"message": "成功加入组织", "organization_id": member.organization_id}
