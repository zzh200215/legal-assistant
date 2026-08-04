"""组织架构同步服务：从企业微信/钉钉/LDAP 同步"""
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.user import User, UserRole, UserStatus
from app.models.org import Organization, Department
from app.core.config import get_settings


settings = get_settings()


class OrgSyncResult:
    """同步结果"""
    def __init__(self):
        self.organizations_created: int = 0
        self.organizations_updated: int = 0
        self.departments_created: int = 0
        self.departments_updated: int = 0
        self.users_created: int = 0
        self.users_updated: int = 0
        self.users_disabled: int = 0
        self.errors: List[str] = []

    def to_dict(self):
        return {
            "organizations_created": self.organizations_created,
            "organizations_updated": self.organizations_updated,
            "departments_created": self.departments_created,
            "departments_updated": self.departments_updated,
            "users_created": self.users_created,
            "users_updated": self.users_updated,
            "users_disabled": self.users_disabled,
            "errors": self.errors,
        }


class OrgSyncService:
    """组织架构同步服务"""

    def sync_from_wecom(self, db: Session, corp_id: Optional[str] = None) -> OrgSyncResult:
        """从企业微信同步组织架构"""
        result = OrgSyncResult()

        # 实际实现需要调用企业微信通讯录 API
        # 1. 获取 access_token
        # 2. 获取部门列表
        # 3. 获取部门成员
        # 4. 同步到本地数据库

        # 示例：模拟同步流程
        # access_token = self._get_wecom_access_token(corp_id)
        # departments = self._get_wecom_departments(access_token)
        # for dept in departments:
        #     self._sync_department(db, dept, "wecom", result)
        #
        # users = self._get_wecom_users(access_token, departments)
        # for user in users:
        #     self._sync_user(db, user, "wecom", result)

        result.errors.append("企业微信同步功能需要配置 API 并实现具体调用逻辑")
        return result

    def sync_from_dingtalk(self, db: Session) -> OrgSyncResult:
        """从钉钉同步组织架构"""
        result = OrgSyncResult()

        # 实际实现需要调用钉钉通讯录 API
        result.errors.append("钉钉同步功能需要配置 API 并实现具体调用逻辑")
        return result

    def sync_from_ldap(self, db: Session) -> OrgSyncResult:
        """从 LDAP 同步组织架构"""
        result = OrgSyncResult()

        # 实际实现需要 ldap3 库
        # from ldap3 import Server, Connection, ALL
        #
        # server = Server(settings.LDAP_URL)
        # conn = Connection(server, settings.LDAP_BIND_DN, settings.LDAP_BIND_PASSWORD, auto_bind=True)
        #
        # # 搜索组织单元
        # conn.search(settings.LDAP_BASE_DN, '(objectClass=organizationalUnit)', attributes=['ou', 'description'])
        # for entry in conn.entries:
        #     self._sync_ldap_ou(db, entry, result)
        #
        # # 搜索用户
        # conn.search(settings.LDAP_BASE_DN, '(objectClass=person)', attributes=['uid', 'cn', 'mail', 'departmentNumber'])
        # for entry in conn.entries:
        #     self._sync_ldap_user(db, entry, result)

        result.errors.append("LDAP 同步功能需要安装 ldap3 并实现具体调用逻辑")
        return result

    def sync_organization(
        self,
        db: Session,
        name: str,
        code: str,
        description: Optional[str] = None,
    ) -> Organization:
        """同步单个组织"""
        org = db.query(Organization).filter(Organization.code == code).first()
        if org:
            if org.name != name:
                org.name = name
                org.description = description
                db.add(org)
            return org

        org = Organization(name=name, code=code, description=description)
        db.add(org)
        db.commit()
        db.refresh(org)
        return org

    def sync_department(
        self,
        db: Session,
        organization_code: str,
        name: str,
        code: str,
        parent_code: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[Department]:
        """同步单个部门"""
        org = db.query(Organization).filter(Organization.code == organization_code).first()
        if not org:
            return None

        dept = db.query(Department).filter(
            and_(
                Department.organization_id == org.id,
                Department.code == code,
            )
        ).first()

        if dept:
            if dept.name != name:
                dept.name = name
                dept.description = description
                db.add(dept)
            return dept

        dept = Department(
            organization_id=org.id,
            name=name,
            code=code,
            description=description,
        )
        db.add(dept)
        db.commit()
        db.refresh(dept)
        return dept

    def sync_user(
        self,
        db: Session,
        provider: str,
        external_user_id: str,
        username: str,
        email: str,
        full_name: Optional[str] = None,
        employee_id: Optional[str] = None,
        organization_code: Optional[str] = None,
        department_code: Optional[str] = None,
        job_title: Optional[str] = None,
        is_active: bool = True,
    ) -> Optional[User]:
        """同步单个用户"""
        # 查找是否已存在
        user = db.query(User).filter(
            and_(
                User.external_provider == provider,
                User.external_user_id == external_user_id,
            )
        ).first()

        # 查找组织和部门
        organization = None
        department = None

        if organization_code:
            organization = db.query(Organization).filter(Organization.code == organization_code).first()

        if department_code and organization:
            department = db.query(Department).filter(
                and_(
                    Department.organization_id == organization.id,
                    Department.code == department_code,
                )
            ).first()

        if user:
            # 更新用户信息
            user.email = email
            user.full_name = full_name
            user.employee_id = employee_id
            user.organization_id = organization.id if organization else None
            user.department_id = department.id if department else None
            user.job_title = job_title
            if not is_active:
                user.status = UserStatus.disabled.value
            else:
                if user.status == UserStatus.disabled.value:
                    user.status = UserStatus.active.value
            db.add(user)
            db.commit()
            db.refresh(user)
            return user

        # 创建新用户
        user = User(
            username=username,
            email=email,
            hashed_password=None,  # 外部账号无本地密码
            full_name=full_name,
            role=UserRole.user.value,
            status=UserStatus.active.value if is_active else UserStatus.disabled.value,
            external_provider=provider,
            external_user_id=external_user_id,
            employee_id=employee_id,
            organization_id=organization.id if organization else None,
            department_id=department.id if department else None,
            job_title=job_title,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    def disable_missing_users(
        self,
        db: Session,
        provider: str,
        active_external_ids: List[str],
    ) -> int:
        """禁用在外部系统中不存在的用户"""
        count = 0
        users = db.query(User).filter(
            and_(
                User.external_provider == provider,
                User.status == UserStatus.active.value,
            )
        ).all()

        for user in users:
            if user.external_user_id not in active_external_ids:
                user.status = UserStatus.disabled.value
                db.add(user)
                count += 1

        if count > 0:
            db.commit()

        return count


org_sync_service = OrgSyncService()