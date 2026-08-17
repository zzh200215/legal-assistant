"""管理员操作审计服务"""
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session

from app.models.auth_log import AdminAuditLog
from app.models.user import User


class AuditAction:
    """审计操作类型"""
    # 用户管理
    USER_CREATE = "user_create"
    USER_UPDATE = "user_update"
    USER_DISABLE = "user_disable"
    USER_ENABLE = "user_enable"
    USER_UNLOCK = "user_unlock"
    USER_DELETE = "user_delete"
    USER_ROLE_CHANGE = "user_role_change"
    USER_PASSWORD_RESET = "user_password_reset"
    USER_FORCE_LOGOUT = "user_force_logout"

    # 组织管理
    ORG_CREATE = "org_create"
    ORG_UPDATE = "org_update"
    ORG_DELETE = "org_delete"

    # 部门管理
    DEPT_CREATE = "dept_create"
    DEPT_UPDATE = "dept_update"
    DEPT_DELETE = "dept_delete"
    DEPT_USER_ASSIGN = "dept_user_assign"

    # 文档管理
    DOC_DELETE = "doc_delete"
    DOC_PERMISSION_CHANGE = "doc_permission_change"

    # 系统配置
    CONFIG_CHANGE = "config_change"
    PROMPT_UPDATE = "prompt_update"
    PROMPT_ROLLBACK = "prompt_rollback"

    # 登录相关
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"


class AuditLogService:
    """审计日志服务"""

    def log(
        self,
        db: Session,
        operator: User,
        action: str,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
        target_name: Optional[str] = None,
        detail: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> AdminAuditLog:
        """记录审计日志（P1：detail 落库前统一脱敏，不落正文/密钥/PII）"""
        from app.core.observability_sanitizer import redact_payload

        safe_detail = redact_payload(detail) if detail else None
        log = AdminAuditLog(
            operator_id=operator.id,
            operator_name=operator.username,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            detail=safe_detail,
            ip_address=ip_address,
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        # 等保差距 #2：STRUCTURED_LOG_JSON_LINES 开启时同步输出 JSON 行
        from app.core.observability import structured_log_json

        structured_log_json(
            source="audit_log", module="audit", action=action, actor=operator.username,
            target_type=target_type, target_id=target_id, target_name=target_name,
            detail=safe_detail, ip_address=ip_address,
        )
        return log

    def log_user_action(
        self,
        db: Session,
        operator: User,
        action: str,
        target_user: User,
        detail: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> AdminAuditLog:
        """记录用户管理操作日志"""
        return self.log(
            db=db,
            operator=operator,
            action=action,
            target_type="user",
            target_id=target_user.id,
            target_name=target_user.username,
            detail=detail,
            ip_address=ip_address,
        )

    def log_org_action(
        self,
        db: Session,
        operator: User,
        action: str,
        org_id: int,
        org_name: str,
        detail: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> AdminAuditLog:
        """记录组织管理操作日志"""
        return self.log(
            db=db,
            operator=operator,
            action=action,
            target_type="organization",
            target_id=org_id,
            target_name=org_name,
            detail=detail,
            ip_address=ip_address,
        )

    def log_dept_action(
        self,
        db: Session,
        operator: User,
        action: str,
        dept_id: int,
        dept_name: str,
        detail: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> AdminAuditLog:
        """记录部门管理操作日志"""
        return self.log(
            db=db,
            operator=operator,
            action=action,
            target_type="department",
            target_id=dept_id,
            target_name=dept_name,
            detail=detail,
            ip_address=ip_address,
        )

    def list_logs(
        self,
        db: Session,
        operator_id: Optional[int] = None,
        action: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AdminAuditLog]:
        """查询审计日志"""
        query = db.query(AdminAuditLog)

        if operator_id:
            query = query.filter(AdminAuditLog.operator_id == operator_id)

        if action:
            query = query.filter(AdminAuditLog.action == action)

        if target_type:
            query = query.filter(AdminAuditLog.target_type == target_type)

        if target_id:
            query = query.filter(AdminAuditLog.target_id == target_id)

        if start_time:
            query = query.filter(AdminAuditLog.created_at >= start_time)

        if end_time:
            query = query.filter(AdminAuditLog.created_at <= end_time)

        return query.order_by(AdminAuditLog.created_at.desc()).limit(limit).all()


audit_log_service = AuditLogService()