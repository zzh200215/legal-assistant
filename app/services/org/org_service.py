from __future__ import annotations

from typing import Optional
from sqlalchemy.orm import Session

from app.models.org import Department, Organization, OrganizationMember, LegalMemberRole
from app.models.user import User


class OrgService:
    def get_organization(self, *, db: Session, org_id: int) -> Optional[Organization]:
        return db.query(Organization).filter(Organization.id == org_id).first()

    def create_organization(
        self, *, db: Session, name: str, code: str, description: Optional[str] = None
    ) -> Organization:
        row = Organization(
            name=name.strip(),
            code=code.strip(),
            description=(description or "").strip() or None
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def update_organization(
        self, *, db: Session, org_id: int, name: Optional[str] = None, description: Optional[str] = None,
        if_match_version: Optional[int] = None,
    ) -> Optional[Organization]:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            return None
        # If-Match 前置版本校验：不匹配抛 StaleDataError（全局映射 409）。
        if if_match_version is not None and int(org.version or 0) != if_match_version:
            from sqlalchemy.orm.exc import StaleDataError
            raise StaleDataError(
                f"Organization {org_id} version mismatch: expected v{if_match_version}, got v{org.version}"
            )
        if name:
            org.name = name.strip()
        if description is not None:
            org.description = description.strip() or None
        db.add(org)
        db.commit()
        db.refresh(org)
        return org

    def delete_organization(self, *, db: Session, org_id: int) -> bool:
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            return False
        db.delete(org)
        db.commit()
        return True

    def list_organizations(self, *, db: Session) -> list[Organization]:
        return db.query(Organization).order_by(Organization.created_at.desc(), Organization.id.desc()).all()

    def get_department(self, *, db: Session, dept_id: int) -> Optional[Department]:
        return db.query(Department).filter(Department.id == dept_id).first()

    def create_department(
        self,
        *,
        db: Session,
        organization_id: int,
        name: str,
        code: str,
        description: Optional[str] = None,
    ) -> Department:
        row = Department(
            organization_id=organization_id,
            name=name.strip(),
            code=code.strip(),
            description=(description or "").strip() or None,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def update_department(
        self, *, db: Session, dept_id: int, name: Optional[str] = None, description: Optional[str] = None
    ) -> Optional[Department]:
        dept = db.query(Department).filter(Department.id == dept_id).first()
        if not dept:
            return None
        if name:
            dept.name = name.strip()
        if description is not None:
            dept.description = description.strip() or None
        db.add(dept)
        db.commit()
        db.refresh(dept)
        return dept

    def delete_department(self, *, db: Session, dept_id: int) -> bool:
        dept = db.query(Department).filter(Department.id == dept_id).first()
        if not dept:
            return False
        db.delete(dept)
        db.commit()
        return True

    def list_departments(self, *, db: Session, organization_id: Optional[int] = None) -> list[Department]:
        query = db.query(Department)
        if organization_id is not None:
            query = query.filter(Department.organization_id == organization_id)
        return query.order_by(Department.created_at.desc(), Department.id.desc()).all()

    def assign_user(
        self,
        *,
        db: Session,
        user_id: int,
        organization_id: Optional[int],
        department_id: Optional[int],
        job_title: Optional[str] = None
    ) -> User:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError("User not found")
        user.organization_id = organization_id
        user.department_id = department_id
        user.job_title = (job_title or "").strip() or None
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def check_user_in_org(self, *, db: Session, user_id: int, org_id: int) -> bool:
        """检查用户是否是组织成员"""
        member = db.query(OrganizationMember).filter(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == user_id,
        ).first()
        return member is not None

    def get_user_org_member(
        self, *, db: Session, user_id: int, org_id: int
    ) -> Optional[OrganizationMember]:
        """获取用户在组织中的成员记录"""
        return db.query(OrganizationMember).filter(
            OrganizationMember.organization_id == org_id,
            OrganizationMember.user_id == user_id,
        ).first()

    def get_user_legal_role(
        self, *, db: Session, user_id: int, org_id: int
    ) -> Optional[LegalMemberRole]:
        """获取用户在组织中的法律业务角色"""
        member = self.get_user_org_member(db=db, user_id=user_id, org_id=org_id)
        if not member:
            return None
        try:
            return LegalMemberRole(member.legal_role)
        except ValueError:
            return None

    def check_user_has_role(
        self,
        *,
        db: Session,
        user_id: int,
        org_id: int,
        min_role: LegalMemberRole
    ) -> bool:
        """检查用户是否拥有指定角色或更高权限

        角色权限层级（从高到低）:
        admin > reviewer > editor > client
        """
        role_hierarchy = {
            LegalMemberRole.admin: 4,
            LegalMemberRole.reviewer: 3,
            LegalMemberRole.editor: 2,
            LegalMemberRole.client: 1,
        }

        user_role = self.get_user_legal_role(db=db, user_id=user_id, org_id=org_id)
        if not user_role:
            return False

        user_level = role_hierarchy.get(user_role, 0)
        required_level = role_hierarchy.get(min_role, 0)

        return user_level >= required_level


org_service = OrgService()