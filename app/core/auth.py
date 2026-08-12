from datetime import datetime, timedelta, timezone
from typing import Callable

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.core.config import get_settings
from app.core.database import get_db
from app.models.user import UserStatus, User
from app.models.org import OrganizationMember, LegalMemberRole
from app.services.auth_token_service import auth_token_service, new_jti
from app.services.org_service import org_service

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
settings = get_settings()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """签发 access JWT（兼容旧签名）。

    最终生成的 token 必然包含 jti 与 token_version，供撤销与版本校验使用。
    """
    to_encode = data.copy()
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    if "jti" not in to_encode:
        to_encode["jti"] = new_jti()
    if "token_version" not in to_encode:
        to_encode["token_version"] = 0
    if "typ" not in to_encode:
        to_encode["typ"] = "access"
    if "iat" not in to_encode:
        to_encode["iat"] = int(now.timestamp())
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict | None:
    """解码 JWT token，返回 payload 或 None"""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


def get_user_from_token(token: str, db: Session) -> User | None:
    payload = decode_token(token)
    if not payload:
        return None
    user_id_str = payload.get("sub")
    if user_id_str is None:
        return None
    try:
        user_id = int(user_id_str)
    except (TypeError, ValueError):
        return None
    return db.query(User).filter(User.id == user_id).first()


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = api_error(
        status.HTTP_401_UNAUTHORIZED,
        "无法验证凭据",
        code="INVALID_CREDENTIALS",
    )
    credentials_exception.headers = {"WWW-Authenticate": "Bearer"}
    # 校验：签名、过期、jti 是否撤销、token_version 是否仍匹配用户。
    user = auth_token_service.validate_access_token(token, db)
    if user is None:
        raise credentials_exception
    # #95：deletion_pending 视为可执行注销流程（撤销/确认），其余状态按禁用处理
    if user.status == UserStatus.deletion_pending.value:
        return user
    if not user.is_active:
        raise api_error(
            status.HTTP_403_FORBIDDEN,
            "账号已被禁用",
            code="USER_DISABLED",
        )
    return user


def get_token_jti(request: Request, token: str | None = None) -> str | None:
    """从请求的 Bearer token 提取 jti（用于构建授权上下文）。"""
    raw = token
    if raw is None and request is not None:
        auth_header = request.headers.get("Authorization") or ""
        if auth_header.startswith("Bearer "):
            raw = auth_header[7:]
    if not raw:
        return None
    payload = auth_token_service.decode_access_token(raw)
    return payload.get("jti") if payload else None


def get_current_context(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    request: Request = None,
) -> "AuthorizationContext":
    """构建统一授权上下文（组织成员关系实时从数据库解析）。"""
    from app.services.authorization_service import authorization_service

    return authorization_service.build_context(
        db,
        current_user,
        org_id=current_user.organization_id,
        jti=get_token_jti(request),
        token_version=current_user.token_version,
    )


def require_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise api_error(
            status.HTTP_403_FORBIDDEN,
            "需要管理员权限",
            code="ADMIN_REQUIRED",
        )
    return current_user


class RequireOrgMember:
    """要求用户是指定组织的成员"""
    def __init__(self, org_id: int):
        self.org_id = org_id

    def __call__(
        self,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> OrganizationMember:
        member = org_service.get_user_org_member(
            db=db,
            user_id=current_user.id,
            org_id=self.org_id
        )
        if not member:
            raise api_error(
                status.HTTP_403_FORBIDDEN,
                "您不是该组织的成员",
                code="NOT_ORG_MEMBER",
            )
        return member


def require_org_member(org_id: int):
    """要求用户是指定组织的成员

    用法:
        @router.get("/orgs/{org_id}/cases")
        def list_cases(
            org_id: int,
            member: OrganizationMember = Depends(RequireOrgMember(org_id)),
        ):
            # member.organization_id 已验证等于 org_id
    """
    return RequireOrgMember(org_id)


class RequireOrgRole:
    """要求用户在组织中拥有指定角色或更高权限"""
    def __init__(self, org_id: int, min_role: LegalMemberRole):
        self.org_id = org_id
        self.min_role = min_role

    def __call__(
        self,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> OrganizationMember:
        member = org_service.get_user_org_member(
            db=db,
            user_id=current_user.id,
            org_id=self.org_id
        )
        if not member:
            raise api_error(
                status.HTTP_403_FORBIDDEN,
                "您不是该组织的成员",
                code="NOT_ORG_MEMBER",
            )

        has_role = org_service.check_user_has_role(
            db=db,
            user_id=current_user.id,
            org_id=self.org_id,
            min_role=self.min_role
        )
        if not has_role:
            raise api_error(
                status.HTTP_403_FORBIDDEN,
                f"需要 {self.min_role.value} 或更高权限",
                code="INSUFFICIENT_ROLE",
            )
        return member


def require_org_role(org_id: int, min_role: LegalMemberRole):
    """要求用户在组织中拥有指定角色或更高权限

    角色层级: admin > reviewer > editor > client

    用法:
        @router.post("/orgs/{org_id}/cases")
        def create_case(
            org_id: int,
            member: OrganizationMember = Depends(require_org_role(org_id, LegalMemberRole.editor)),
        ):
            # 确保用户至少是 editor 角色
    """
    return RequireOrgRole(org_id, min_role)


class RequireCaseAccess:
    """要求用户对指定案件有访问权限"""
    def __init__(self, case_id: int):
        self.case_id = case_id

    def __call__(
        self,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> dict:
        # 依赖注入和端点内手动校验必须共用同一规则，尤其是严格案件成员撤销。
        return verify_case_access(self.case_id, current_user.id, db)


def require_case_access(case_id: int):
    """要求用户对指定案件有访问权限

    用法:
        @router.get("/cases/{case_id}")
        def get_case(
            case_id: int,
            case_info: dict = Depends(require_case_access(case_id)),
        ):
            # case_info 包含验证过的案件和成员信息
    """
    return RequireCaseAccess(case_id)


class RequireResourceScope:
    """通用资源级权限检查（用于合同、账单等）

    从资源反查组织ID，验证用户是该组织成员

    用法:
        @router.get("/contracts/{contract_id}")
        def get_contract(
            contract_id: int,
            scope: dict = Depends(require_resource_scope("contract", "contract_id")),
            db: Session = Depends(get_db)
        ):
            # scope 包含验证过的资源和成员信息
    """
    def __init__(self, resource_type: str, resource_id_param: str):
        self.resource_type = resource_type
        self.resource_id_param = resource_id_param

    def __call__(
        self,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
        request: Request = None,
    ) -> dict:
        resource_id = request.path_params.get(self.resource_id_param) if request else None
        try:
            resource_id = int(resource_id)
        except (TypeError, ValueError):
            raise api_error(
                status.HTTP_400_BAD_REQUEST,
                f"缺少或无效参数: {self.resource_id_param}",
                code="MISSING_PARAMETER",
            )
        return verify_resource_access(self.resource_type, resource_id, current_user.id, db)


def require_resource_scope(
    resource_type: str,
    resource_id_param: str = "resource_id",
) -> Callable:
    """创建资源级权限依赖，从路径参数读取资源 ID。"""
    return RequireResourceScope(resource_type, resource_id_param)


# ── 手动验证函数（非依赖注入版本）───────────────────────────────────────────


def verify_org_member_access(org_id: int, user_id: int, db: Session) -> OrganizationMember:
    """验证用户是组织成员（手动调用版本）

    用于在端点函数内部手动验证权限，不使用依赖注入。
    """
    member = org_service.get_user_org_member(db=db, user_id=user_id, org_id=org_id)
    if not member:
        raise api_error(
            status.HTTP_403_FORBIDDEN,
            "您不是该组织的成员",
            code="NOT_ORG_MEMBER",
        )
    return member


def verify_org_role_access(
    org_id: int,
    user_id: int,
    min_role: LegalMemberRole,
    db: Session
) -> OrganizationMember:
    """验证用户角色权限（手动调用版本）

    验证用户在组织中拥有指定角色或更高权限。
    角色层级：admin > reviewer > editor > client
    """
    member = verify_org_member_access(org_id, user_id, db)

    if not org_service.check_user_has_role(
        db=db,
        user_id=user_id,
        org_id=org_id,
        min_role=min_role
    ):
        raise api_error(
            status.HTTP_403_FORBIDDEN,
            f"需要 {min_role.value} 或更高权限",
            code="INSUFFICIENT_ROLE",
        )
    return member


def verify_case_access(case_id: int, user_id: int, db: Session) -> dict:
    """验证案件访问权限（手动调用版本）

    验证逻辑：
    1. 案件必须存在
    2. 用户必须是案件所属组织的成员
    3. 严格模式（is_strict_mode=1）下必须是未撤销的案件成员；普通模式下组织成员均可访问（设计意图）

    返回包含案件、成员和组织ID的字典。
    """
    from app.models.legal import LegalCase

    case = db.query(LegalCase).filter(LegalCase.id == case_id).first()
    if not case:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "案件不存在",
            code="CASE_NOT_FOUND",
        )

    member = org_service.get_user_org_member(
        db=db,
        user_id=user_id,
        org_id=case.organization_id
    )
    if not member:
        # 返回404而不是403，避免泄露案件存在性
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "案件不存在",
            code="CASE_NOT_FOUND",
        )

    # 严格案件不因组织成员身份而放宽；撤销成员立即失效。
    if getattr(case, "is_strict_mode", 0):
        from app.models.legal_portal import LegalCaseMember
        case_member = db.query(LegalCaseMember).filter(
            LegalCaseMember.case_id == case.id,
            LegalCaseMember.user_id == user_id,
            LegalCaseMember.revoked_at.is_(None),
        ).first()
        if not case_member:
            raise api_error(
                status.HTTP_404_NOT_FOUND,
                "案件不存在",
                code="CASE_NOT_FOUND",
            )

    return {
        "case": case,
        "member": member,
        "organization_id": case.organization_id
    }


def verify_resource_access(
    resource_type: str,
    resource_id: int,
    user_id: int,
    db: Session,
    min_role: LegalMemberRole | None = None,
) -> dict:
    """验证组织资源和关联案件的可见性。

    跨组织、严格案件非成员和角色不足均按资源不存在处理，防止通过
    直接枚举资源 ID 推断其他组织的数据。
    """
    from app.models.legal_contract import LegalContract
    from app.models.legal_billing import LegalInvoice, LegalTimeEntry

    resource_models = {
        "contract": LegalContract,
        "invoice": LegalInvoice,
        "time_entry": LegalTimeEntry,
    }
    model = resource_models.get(resource_type)
    if not model:
        raise ValueError(f"不支持的资源类型: {resource_type}")

    resource = db.query(model).filter(model.id == resource_id).first()
    if not resource:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "资源不存在",
            code="RESOURCE_NOT_FOUND",
        )

    member = org_service.get_user_org_member(
        db=db,
        user_id=user_id,
        org_id=resource.organization_id,
    )
    if not member:
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "资源不存在",
            code="RESOURCE_NOT_FOUND",
        )

    case_id = getattr(resource, "case_id", None)
    if case_id:
        try:
            verify_case_access(case_id, user_id, db)
        except HTTPException as exc:
            # api_error 是 HTTPException 的子类；此处隐藏关联案件的存在性。
            if exc.status_code not in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND):
                raise
            raise api_error(
                status.HTTP_404_NOT_FOUND,
                "资源不存在",
                code="RESOURCE_NOT_FOUND",
            ) from exc

    if min_role and not org_service.check_user_has_role(
        db=db,
        user_id=user_id,
        org_id=resource.organization_id,
        min_role=min_role,
    ):
        raise api_error(
            status.HTTP_404_NOT_FOUND,
            "资源不存在",
            code="RESOURCE_NOT_FOUND",
        )

    return {
        "resource": resource,
        "member": member,
        "organization_id": resource.organization_id,
    }
