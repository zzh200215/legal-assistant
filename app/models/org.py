import enum

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class LegalMemberRole(str, enum.Enum):
    """法律业务专用角色（律所/企业法务场景）

    与User.role（系统级admin/dept_admin/user）独立：
    一个系统普通用户可以在律所组织中担任Reviewer（审核律师）。
    """
    admin = "admin"          # 组织管理员：管理成员、配置、账单
    reviewer = "reviewer"    # 审核律师：终审、批注、决定交付
    editor = "editor"        # 律师助理/实习律师：起草、检索、初稿
    client = "client"        # 客户：提交咨询、上传合同、查看草稿


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    departments = relationship("Department", back_populates="organization")
    members = relationship("OrganizationMember", back_populates="organization")


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False, index=True)
    code = Column(String(64), nullable=False, index=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    organization = relationship("Organization", back_populates="departments")


class OrganizationMember(Base):
    """法律业务成员资格

    记录 User 在 Organization 中的角色，与系统级 User.role 独立。
    同一用户可以同时是系统 user 和某律所的 reviewer。
    """

    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_member"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    legal_role = Column(String(32), nullable=False, default=LegalMemberRole.client.value)
    invited_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    invite_token = Column(String(128), nullable=True, index=True, unique=True)
    joined_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    organization = relationship("Organization", back_populates="members")
