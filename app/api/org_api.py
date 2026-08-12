"""组织架构管理 API"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from typing import Optional

from app.core.api_response import api_error, should_passthrough_exception
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User, UserRole
from app.schemas.org import (
    DepartmentCreate, DepartmentOut, DepartmentUpdate,
    OrganizationCreate, OrganizationOut, OrganizationUpdate,
    UserOrgAssignRequest,
)
from app.services.org_service import org_service
from app.services.audit_log_service import audit_log_service, AuditAction

router = APIRouter()


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求管理员权限"""
    if current_user.role not in (UserRole.admin.value, UserRole.dept_admin.value):
        raise api_error(403, "需要管理员权限", code="ADMIN_REQUIRED")
    return current_user


def require_system_admin(current_user: User = Depends(get_current_user)) -> User:
    """要求系统管理员权限"""
    if current_user.role != UserRole.admin.value:
        raise api_error(403, "需要系统管理员权限", code="SYSTEM_ADMIN_REQUIRED")
    return current_user


def get_client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


# ================== 组织管理 ==================

@router.get("/organizations", response_model=list[OrganizationOut])
def list_organizations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询组织列表"""
    return org_service.list_organizations(db=db)


@router.get("/organizations/{org_id}", response_model=OrganizationOut)
def get_organization(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取组织详情"""
    org = org_service.get_organization(db=db, org_id=org_id)
    if not org:
        raise api_error(404, "组织不存在", code="ORG_NOT_FOUND")
    return org


@router.post("/organizations", response_model=OrganizationOut)
def create_organization(
    req: OrganizationCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_system_admin),
):
    """创建组织（系统管理员）"""
    try:
        org = org_service.create_organization(
            db=db, name=req.name, code=req.code, description=req.description
        )
        audit_log_service.log_org_action(
            db=db, operator=admin, action=AuditAction.ORG_CREATE,
            org_id=org.id, org_name=org.name,
            ip_address=get_client_ip(request)
        )
        return org
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "组织创建失败", code="ORG_CREATE_FAILED", detail=str(e))


@router.put("/organizations/{org_id}", response_model=OrganizationOut)
def update_organization(
    org_id: int,
    req: OrganizationUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_system_admin),
):
    """更新组织（系统管理员）"""
    org = org_service.update_organization(
        db=db, org_id=org_id,
        name=req.name, description=req.description
    )
    if not org:
        raise api_error(404, "组织不存在", code="ORG_NOT_FOUND")

    audit_log_service.log_org_action(
        db=db, operator=admin, action=AuditAction.ORG_UPDATE,
        org_id=org.id, org_name=org.name,
        ip_address=get_client_ip(request)
    )
    return org


@router.delete("/organizations/{org_id}")
def delete_organization(
    org_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_system_admin),
):
    """删除组织（系统管理员）"""
    org = org_service.get_organization(db=db, org_id=org_id)
    if not org:
        raise api_error(404, "组织不存在", code="ORG_NOT_FOUND")

    org_service.delete_organization(db=db, org_id=org_id)

    audit_log_service.log_org_action(
        db=db, operator=admin, action=AuditAction.ORG_DELETE,
        org_id=org_id, org_name=org.name,
        ip_address=get_client_ip(request)
    )
    return {"id": org_id, "message": "组织已删除"}


# ================== 部门管理 ==================

@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(
    organization_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询部门列表"""
    return org_service.list_departments(db=db, organization_id=organization_id)


@router.get("/departments/{dept_id}", response_model=DepartmentOut)
def get_department(
    dept_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取部门详情"""
    dept = org_service.get_department(db=db, dept_id=dept_id)
    if not dept:
        raise api_error(404, "部门不存在", code="DEPT_NOT_FOUND")
    return dept


@router.post("/departments", response_model=DepartmentOut)
def create_department(
    req: DepartmentCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """创建部门"""
    # 部门管理员只能在本组织内创建
    if admin.role == UserRole.dept_admin.value:
        if admin.organization_id != req.organization_id:
            raise api_error(403, "只能在本组织内创建部门", code="PERMISSION_DENIED")

    try:
        dept = org_service.create_department(
            db=db,
            organization_id=req.organization_id,
            name=req.name,
            code=req.code,
            description=req.description,
        )
        audit_log_service.log_dept_action(
            db=db, operator=admin, action=AuditAction.DEPT_CREATE,
            dept_id=dept.id, dept_name=dept.name,
            ip_address=get_client_ip(request)
        )
        return dept
    except Exception as e:
        if should_passthrough_exception(e):
            raise
        raise api_error(500, "部门创建失败", code="DEPT_CREATE_FAILED", detail=str(e))


@router.put("/departments/{dept_id}", response_model=DepartmentOut)
def update_department(
    dept_id: int,
    req: DepartmentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """更新部门"""
    dept = org_service.get_department(db=db, dept_id=dept_id)
    if not dept:
        raise api_error(404, "部门不存在", code="DEPT_NOT_FOUND")

    # 部门管理员只能修改本部门
    if admin.role == UserRole.dept_admin.value:
        if admin.department_id != dept_id:
            raise api_error(403, "只能修改本部门", code="PERMISSION_DENIED")

    dept = org_service.update_department(
        db=db, dept_id=dept_id,
        name=req.name, description=req.description
    )

    audit_log_service.log_dept_action(
        db=db, operator=admin, action=AuditAction.DEPT_UPDATE,
        dept_id=dept.id, dept_name=dept.name,
        ip_address=get_client_ip(request)
    )
    return dept


@router.delete("/departments/{dept_id}")
def delete_department(
    dept_id: int,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_system_admin),
):
    """删除部门（系统管理员）"""
    dept = org_service.get_department(db=db, dept_id=dept_id)
    if not dept:
        raise api_error(404, "部门不存在", code="DEPT_NOT_FOUND")

    org_service.delete_department(db=db, dept_id=dept_id)

    audit_log_service.log_dept_action(
        db=db, operator=admin, action=AuditAction.DEPT_DELETE,
        dept_id=dept_id, dept_name=dept.name,
        ip_address=get_client_ip(request)
    )
    return {"id": dept_id, "message": "部门已删除"}


# ================== 用户归属分配 ==================

@router.post("/users/{user_id}/assign")
def assign_user_org(
    user_id: int,
    req: UserOrgAssignRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """分配用户到组织/部门"""
    from app.models.user import User
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise api_error(404, "用户不存在", code="USER_NOT_FOUND")

    # 部门管理员只能分配到本组织/部门
    if admin.role == UserRole.dept_admin.value:
        if req.organization_id != admin.organization_id:
            raise api_error(403, "只能分配到本组织", code="PERMISSION_DENIED")

    user = org_service.assign_user(
        db=db,
        user_id=user_id,
        organization_id=req.organization_id,
        department_id=req.department_id,
        job_title=req.job_title,
    )

    audit_log_service.log_user_action(
        db=db, operator=admin, action=AuditAction.DEPT_USER_ASSIGN,
        target_user=user,
        detail=f"分配到组织 {req.organization_id} 部门 {req.department_id}",
        ip_address=get_client_ip(request)
    )

    return {
        "id": user.id,
        "organization_id": user.organization_id,
        "department_id": user.department_id,
        "job_title": user.job_title,
    }