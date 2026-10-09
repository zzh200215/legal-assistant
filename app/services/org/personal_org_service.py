"""注册自动创建个人组织（ux-audit P0-1 根治）。

自注册用户不再是无组织状态：注册成功即拥有一个 personal 组织并担任 admin，
工作台/案件/咨询等组织上下文操作全部直接可用。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.org import LegalMemberRole, Organization, OrganizationMember
from app.models.user import User


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_personal_org(db: Session, user: User) -> Organization:
    """为用户确保存在个人组织；已归属组织则直接返回该组织（幂等）。

    在同一事务内创建 Organization + OrganizationMember(admin) 并回写
    user.organization_id；**不 commit**——事务边界由调用方（注册端点）统一控制。
    """
    if user.organization_id is not None:
        existing = db.query(Organization).filter(Organization.id == user.organization_id).first()
        if existing is not None:
            return existing
        # organization_id 悬挂（指向已删除组织）：视为无组织，走下方重建。

    db.flush()  # 确保 user.id 已分配（注册端点 add User 后尚未 commit）

    display = (user.full_name or user.username or "").strip() or "用户"
    name = f"{display}的工作台"
    # name 撞 organizations.name 唯一索引（不同用户同名 full_name）：预防式加唯一后缀。
    # （用查询先行而非 IntegrityError 重试：事务中途回滚会使未提交的 user 一并失效。）
    if db.query(Organization.id).filter(Organization.name == name).first() is not None:
        name = f"{display}({user.id})的工作台"

    org = Organization(
        name=name,
        code=f"personal-{user.id}",  # user.id 唯一 → code 天然唯一
        org_type="personal",
        description=f"{display}的个人法律工作台（注册自动创建）",
    )
    db.add(org)
    db.flush()  # 分配 org.id

    db.add(OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        legal_role=LegalMemberRole.admin.value,
        joined_at=_now(),
    ))
    user.organization_id = org.id
    return org
