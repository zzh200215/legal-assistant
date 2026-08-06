"""Phase 12 — 合同台账 / 版本 / 电子签名 / 审查策略 API"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import (
    get_current_user,
    verify_case_access,
    verify_org_member_access,
    verify_org_role_access,
    verify_resource_access,
)
from app.services.org_service import org_service
from app.core.config import get_settings
from app.core.error_codes import err, CONTRACT_VERSION_LOCKED
from app.models.user import User
from app.models.org import OrganizationMember, LegalMemberRole
from app.models.legal import LegalCase
from app.models.legal_contract import (
    LegalContract, LegalContractVersion, LegalContractMilestone,
    LegalSignRequest, LegalSignParty, LegalSignEvent,
    LegalReviewPolicy, LegalReviewPolicyVersion,
)
from app.services.oplog_service import oplog_service

router = APIRouter()


def verify_contract_access(contract_id: int, user: User, db: Session) -> dict:
    """验证用户对合同的访问权限，返回404而非403避免资源泄露"""
    return verify_resource_access("contract", contract_id, user.id, db)


def require_signing_provider_configured() -> None:
    """电子签署尚未接入真实服务商（EXT-01），未配置提供方时拒绝发起/发送。"""
    settings = get_settings()
    if not (
        str(getattr(settings, "SIGNING_FADADA_SANDBOX_URL", "")).strip()
        and str(getattr(settings, "SIGNING_FADADA_API_KEY", "")).strip()
    ):
        raise HTTPException(503, "电子签署服务未配置（EXT-01），试点阶段暂不可用")


@router.get("/features")
def get_feature_flags(current_user: User = Depends(get_current_user)):
    """返回按部署配置控制的特性开关，供前端隐藏未就绪入口。"""
    settings = get_settings()
    return {
        "signing_enabled": bool(
            str(getattr(settings, "SIGNING_FADADA_SANDBOX_URL", "")).strip()
            and str(getattr(settings, "SIGNING_FADADA_API_KEY", "")).strip()
        ),
        "open_api_enabled": bool(getattr(settings, "OPEN_API_ENABLED", False)),
    }


def require_contract_editor(contract_id: int, user: User, db: Session) -> dict:
    """验证用户对合同的编辑权限（至少editor角色）"""
    return verify_resource_access(
        "contract", contract_id, user.id, db, min_role=LegalMemberRole.editor
    )


# ── Contracts ─────────────────────────────────────────────────────────────────

class ContractCreate(BaseModel):
    case_id: Optional[int] = None
    title: str = Field(..., max_length=256)
    counterparty: Optional[str] = Field(None, max_length=256)
    contract_type: Optional[str] = None
    contract_no: Optional[str] = Field(None, max_length=64)
    description: Optional[str] = None


def _gen_contract_no(org_id: int, db: Session) -> str:
    count = db.query(LegalContract).filter(LegalContract.organization_id == org_id).count()
    return f"CON-{org_id}-{count + 1:06d}"


@router.post("/orgs/{org_id}/contracts")
def create_contract(
    org_id: int,
    body: ContractCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.editor, db)
    if body.case_id:
        case_scope = verify_case_access(body.case_id, current_user.id, db)
        if case_scope["organization_id"] != org_id:
            raise HTTPException(404, detail="案件不存在")
    contract_no = body.contract_no or _gen_contract_no(org_id, db)
    # 组织内合同编号唯一性检查
    existing = db.query(LegalContract).filter(
        LegalContract.organization_id == org_id,
        LegalContract.contract_no == contract_no,
    ).first()
    if existing:
        raise HTTPException(409, detail="合同编号在本组织内已存在")

    contract = LegalContract(
        organization_id=org_id,
        case_id=body.case_id,
        contract_no=contract_no,
        title=body.title,
        counterparty=body.counterparty,
        contract_type=body.contract_type,
        description=body.description,
        created_by=current_user.id,
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return contract


@router.get("/orgs/{org_id}/contracts")
def list_contracts(
    org_id: int,
    case_id: Optional[int] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_member_access(org_id, current_user.id, db)
    query = db.query(LegalContract).filter(LegalContract.organization_id == org_id)
    if case_id:
        query = query.filter(LegalContract.case_id == case_id)
    if status:
        query = query.filter(LegalContract.status == status)
    if q:
        query = query.filter(LegalContract.title.contains(q))
    # 严格案件合同对非案件成员不可见；普通合同维持组织级可见性。
    from app.models.legal_portal import LegalCaseMember
    candidates = query.order_by(LegalContract.updated_at.desc()).all()
    visible = []
    for contract in candidates:
        if not contract.case_id:
            visible.append(contract)
            continue
        case = db.query(LegalCase).filter(LegalCase.id == contract.case_id).first()
        if not case or not getattr(case, "is_strict_mode", 0):
            visible.append(contract)
            continue
        is_case_member = db.query(LegalCaseMember).filter(
            LegalCaseMember.case_id == case.id,
            LegalCaseMember.user_id == current_user.id,
            LegalCaseMember.revoked_at.is_(None),
        ).first()
        if is_case_member:
            visible.append(contract)
    total = len(visible)
    items = visible[(page - 1) * page_size:page * page_size]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/contracts/expiry-alerts")
def expiry_alerts(
    org_id: int,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_member_access(org_id, current_user.id, db)
    q = db.query(LegalContractMilestone).filter(
        LegalContractMilestone.organization_id == org_id,
        LegalContractMilestone.milestone_type.in_(["expiry", "renewal"]),
        LegalContractMilestone.status == "confirmed",
    ).order_by(LegalContractMilestone.standard_date)
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/contracts/{contract_id}")
def get_contract(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scope = verify_contract_access(contract_id, current_user, db)
    return scope["resource"]


# ── Contract Versions ─────────────────────────────────────────────────────────

class VersionCreate(BaseModel):
    source_type: str = Field("text_snapshot", pattern="^(document|text_snapshot|contract_review)$")
    source_document_id: Optional[int] = None
    source_review_id: Optional[int] = None
    text_snapshot: Optional[str] = None
    version_note: Optional[str] = None


@router.post("/contracts/{contract_id}/versions")
def create_contract_version(
    contract_id: int,
    body: VersionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scope = require_contract_editor(contract_id, current_user, db)
    contract = scope["resource"]

    # 版本锁定守卫：已签署或签署中的版本不可被新版本覆盖
    locked = db.query(LegalSignRequest).filter(
        LegalSignRequest.contract_id == contract_id,
        LegalSignRequest.status.in_(["signed", "pending_sign"]),
    ).first()
    if locked:
        raise HTTPException(409, detail=err(CONTRACT_VERSION_LOCKED))

    # 每种来源必须引用且只引用一个可访问的真实资源，文本快照例外。
    if body.source_type == "document":
        if not body.source_document_id or body.source_review_id or body.text_snapshot:
            raise HTTPException(422, detail="文件版本必须只提供 source_document_id")
        from app.models.document import Document
        document = db.query(Document).filter(
            Document.id == body.source_document_id,
            Document.organization_id == contract.organization_id,
        ).first()
        if not document:
            raise HTTPException(404, detail="源文件不存在或无权访问")
    elif body.source_type == "contract_review":
        if not body.source_review_id or body.source_document_id or body.text_snapshot:
            raise HTTPException(422, detail="审查版本必须只提供 source_review_id")
        from app.models.legal import ContractReview
        review = db.query(ContractReview).filter(
            ContractReview.id == body.source_review_id,
            ContractReview.user_id == current_user.id,
        ).first()
        if not review:
            raise HTTPException(404, detail="源审查不存在或无权访问")
        body.text_snapshot = review.content
    elif not body.text_snapshot or body.source_document_id or body.source_review_id:
        raise HTTPException(422, detail="文本版本必须只提供 text_snapshot")

    last_version = db.query(LegalContractVersion).filter(
        LegalContractVersion.contract_id == contract_id
    ).order_by(LegalContractVersion.version_no.desc()).first()

    next_no = (last_version.version_no + 1) if last_version else 1

    content_hash = None
    if body.text_snapshot:
        content_hash = hashlib.sha256(body.text_snapshot.encode()).hexdigest()

    version = LegalContractVersion(
        contract_id=contract_id,
        organization_id=contract.organization_id,
        version_no=next_no,
        source_type=body.source_type,
        source_document_id=body.source_document_id,
        source_review_id=body.source_review_id,
        content_hash=content_hash,
        text_snapshot=body.text_snapshot,
        parse_status="uploading",
        version_note=body.version_note,
        created_by=current_user.id,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


@router.post("/contracts/{contract_id}/versions/{version_id}/confirm")
def confirm_contract_version(
    contract_id: int,
    version_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """确认已解析候选版本为当前版本，保留历史版本不改写。"""
    contract = require_contract_editor(contract_id, current_user, db)["resource"]
    version = db.query(LegalContractVersion).filter(
        LegalContractVersion.id == version_id,
        LegalContractVersion.contract_id == contract_id,
    ).first()
    if not version:
        raise HTTPException(404, detail="合同版本不存在")
    if version.parse_status not in ("ready", "needs_confirmation"):
        raise HTTPException(409, detail="版本尚未完成解析")
    db.query(LegalContractVersion).filter(
        LegalContractVersion.contract_id == contract_id,
        LegalContractVersion.is_current == 1,
    ).update({LegalContractVersion.is_current: 0}, synchronize_session=False)
    version.is_current = 1
    contract.current_version_id = version.id
    db.commit()
    db.refresh(version)
    return version


@router.get("/contracts/{contract_id}/diff")
def contract_diff(
    contract_id: int,
    base_version: int,
    target_version: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_contract_access(contract_id, current_user, db)
    from app.services.contract_diff_service import compute_diff
    base = db.query(LegalContractVersion).filter(
        LegalContractVersion.contract_id == contract_id,
        LegalContractVersion.version_no == base_version,
    ).first()
    target = db.query(LegalContractVersion).filter(
        LegalContractVersion.contract_id == contract_id,
        LegalContractVersion.version_no == target_version,
    ).first()
    if not base or not target:
        raise HTTPException(404, detail="指定版本不存在")
    if base.parse_status not in ("ready", "needs_confirmation") or \
       target.parse_status not in ("ready", "needs_confirmation"):
        raise HTTPException(409, detail="版本解析未完成，无法执行 Diff")
    return compute_diff(base.id, target.id, db)


@router.get("/contracts/{contract_id}/versions")
def list_contract_versions(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_contract_access(contract_id, current_user, db)
    return db.query(LegalContractVersion).filter(
        LegalContractVersion.contract_id == contract_id
    ).order_by(LegalContractVersion.version_no.desc()).all()


# ── Contract Milestones ───────────────────────────────────────────────────────

@router.get("/contracts/{contract_id}/milestones")
def list_milestones(
    contract_id: int,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_contract_access(contract_id, current_user, db)
    q = db.query(LegalContractMilestone).filter(
        LegalContractMilestone.contract_id == contract_id
    )
    if status:
        q = q.filter(LegalContractMilestone.status == status)
    return q.all()


@router.post("/contracts/{contract_id}/milestones/{milestone_id}/confirm")
def confirm_milestone(
    contract_id: int,
    milestone_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_contract_editor(contract_id, current_user, db)

    ms = db.query(LegalContractMilestone).filter(
        LegalContractMilestone.id == milestone_id,
        LegalContractMilestone.contract_id == contract_id,
    ).first()
    if not ms:
        raise HTTPException(404)
    ms.status = "confirmed"
    ms.confirmed_by = current_user.id
    ms.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(ms)
    return ms


# ── Sign Requests ─────────────────────────────────────────────────────────────

class SignRequestCreate(BaseModel):
    contract_version_id: int
    deadline_at: Optional[datetime] = None
    parties: list


@router.post("/contracts/{contract_id}/sign-requests")
def create_sign_request(
    contract_id: int,
    body: SignRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_signing_provider_configured()
    scope = require_contract_editor(contract_id, current_user, db)
    contract = scope["resource"]

    if contract.status in ("terminated", "voided", "expired"):
        raise HTTPException(400, detail=f"合同状态为{contract.status}，不可发起签署")

    version = db.query(LegalContractVersion).filter(
        LegalContractVersion.id == body.contract_version_id,
        LegalContractVersion.contract_id == contract_id,
        LegalContractVersion.organization_id == contract.organization_id,
    ).first()
    if not version:
        raise HTTPException(400, detail="签署版本不属于该合同")
    if version.parse_status != "ready":
        raise HTTPException(400, detail=f"合同版本解析状态为{version.parse_status}，仅已确认（ready）版本可发起签署")
    if not body.parties:
        raise HTTPException(400, detail="至少需要一名签署方")
    for party in body.parties:
        if not str(party.get("name") or "").strip():
            raise HTTPException(400, detail="每位签署方必须提供姓名")

    req = LegalSignRequest(
        contract_id=contract_id,
        contract_version_id=body.contract_version_id,
        organization_id=contract.organization_id,
        provider="fadada",
        deadline_at=body.deadline_at,
        initiated_by=current_user.id,
    )
    db.add(req)
    db.flush()

    for i, party in enumerate(body.parties):
        db.add(LegalSignParty(
            sign_request_id=req.id,
            name=party.get("name"),
            phone_masked=party.get("phone_masked"),
            sign_order=party.get("sign_order", i + 1),
            # 持久化服务商签署方ID：供回调 rejected 分支按 party 匹配（此前从未写入导致死逻辑）
            provider_sign_id=party.get("provider_sign_id"),
        ))

    db.commit()
    db.refresh(req)
    return req


@router.get("/contracts/{contract_id}/sign-requests")
def list_sign_requests(
    contract_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_contract_access(contract_id, current_user, db)
    return db.query(LegalSignRequest).filter(
        LegalSignRequest.contract_id == contract_id
    ).order_by(LegalSignRequest.created_at.desc()).all()


@router.post("/contracts/{contract_id}/apply-review-suggestions")
def apply_review_suggestions(
    contract_id: int,
    review_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从合同审查风险点生成候选修改版本草稿（不修改当前版本）。"""
    from app.models.legal import ContractReview

    scope = require_contract_editor(contract_id, current_user, db)
    contract = scope["resource"]

    review = db.query(ContractReview).filter(ContractReview.id == review_id).first()
    if not review:
        raise HTTPException(404, detail="关联的合同审查不存在")

    # 获取当前版本作为基础
    current_ver = db.query(LegalContractVersion).filter(
        LegalContractVersion.contract_id == contract_id,
        LegalContractVersion.is_current == 1,
    ).first()

    last = db.query(LegalContractVersion).filter(
        LegalContractVersion.contract_id == contract_id
    ).order_by(LegalContractVersion.version_no.desc()).first()
    next_no = (last.version_no + 1) if last else 1

    candidate = LegalContractVersion(
        contract_id=contract_id,
        organization_id=contract.organization_id,
        version_no=next_no,
        source_type="contract_review",
        source_review_id=review_id,
        source_document_id=current_ver.source_document_id if current_ver else None,
        text_snapshot=current_ver.text_snapshot if current_ver else None,
        parse_status="needs_confirmation",
        version_note=f"基于审查#{review_id}的建议修改稿（待人工确认）",
        created_by=current_user.id,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return {"version_id": candidate.id, "version_no": candidate.version_no,
            "parse_status": candidate.parse_status,
            "message": "候选修改稿已创建，请人工确认后再设为当前版本"}


@router.post("/sign-requests/{request_id}/create-revision")
def create_sign_revision(
    request_id: int,
    note: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """拒签/过期后创建替代签署请求（与新合同版本关联）。"""
    original = db.query(LegalSignRequest).filter(LegalSignRequest.id == request_id).first()
    if not original:
        raise HTTPException(404)

    # 检查用户权限（org_service 已模块级导入，避免重复 import 触发 F811）
    member = org_service.get_user_org_member(
        db=db,
        user_id=current_user.id,
        org_id=original.organization_id
    )
    if not member:
        raise HTTPException(404)  # 返回404避免资源泄露

    # 需要至少editor角色
    has_role = org_service.check_user_has_role(
        db=db,
        user_id=current_user.id,
        org_id=original.organization_id,
        min_role=LegalMemberRole.editor
    )
    if not has_role:
        raise HTTPException(404)  # 返回404避免资源泄露

    if original.status not in ("rejected", "expired"):
        raise HTTPException(409, detail="只能对已拒签或已过期的签署请求发起修订")

    contract = db.query(LegalContract).filter(LegalContract.id == original.contract_id).first()
    if not contract:
        raise HTTPException(404)

    # 版本锁定：新签署请求必须引用一个未锁定版本（此处复用当前版本）
    last = db.query(LegalContractVersion).filter(
        LegalContractVersion.contract_id == original.contract_id
    ).order_by(LegalContractVersion.version_no.desc()).first()
    if not last:
        raise HTTPException(400, detail="没有可关联的合同版本")

    new_req = LegalSignRequest(
        contract_id=original.contract_id,
        contract_version_id=last.id,
        organization_id=original.organization_id,
        provider=original.provider,
        replaces_request_id=request_id,
        initiated_by=current_user.id,
    )
    db.add(new_req)
    db.commit()
    db.refresh(new_req)
    return {"new_request_id": new_req.id, "replaces_request_id": request_id,
            "status": new_req.status}


@router.post("/sign-requests/{request_id}/send")
def send_sign_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_signing_provider_configured()
    req = db.query(LegalSignRequest).filter(LegalSignRequest.id == request_id).first()
    if not req:
        raise HTTPException(404)

    # 检查用户权限（org_service 已模块级导入，避免重复 import 触发 F811）
    member = org_service.get_user_org_member(
        db=db,
        user_id=current_user.id,
        org_id=req.organization_id
    )
    if not member:
        raise HTTPException(404)  # 返回404避免资源泄露

    # 需要至少editor角色
    has_role = org_service.check_user_has_role(
        db=db,
        user_id=current_user.id,
        org_id=req.organization_id,
        min_role=LegalMemberRole.editor
    )
    if not has_role:
        raise HTTPException(404)  # 返回404避免资源泄露

    if req.status != "draft":
        raise HTTPException(409, detail=f"签署请求当前状态为 {req.status}，不可重复发送")

    contract = db.query(LegalContract).filter(LegalContract.id == req.contract_id).first()
    if contract and contract.case_id:
        case = db.query(LegalCase).filter(LegalCase.id == contract.case_id).first()
        if case and case.status == "archived":
            raise HTTPException(400, detail="关联案件已归档，不可发送签署请求")

    from app.models.legal import LegalApprovalChain
    approved_chain = db.query(LegalApprovalChain).filter(
        LegalApprovalChain.target_type == "sign_request",
        LegalApprovalChain.target_id == req.id,
        LegalApprovalChain.status == "approved",
    ).first() or db.query(LegalApprovalChain).filter(
        LegalApprovalChain.target_type == "contract",
        LegalApprovalChain.target_id == req.contract_id,
        LegalApprovalChain.status == "approved",
    ).first()

    from app.services import security_audit_service
    if not approved_chain:
        is_admin = org_service.check_user_has_role(
            db=db, user_id=current_user.id, org_id=req.organization_id,
            min_role=LegalMemberRole.admin,
        )
        if not is_admin:
            raise HTTPException(403, detail="发送签署请求前需审批通过，或由组织管理员二次确认发送")
        security_audit_service.write_event(
            event_type="admin_view", actor_type="user", result="success",
            organization_id=req.organization_id, actor_id=str(current_user.id),
            target_type="sign_request", target_id=str(req.id),
            detail_json_hash="send_without_approval_chain:admin_override",
            db=db,
        )

    parties = db.query(LegalSignParty).filter(LegalSignParty.sign_request_id == req.id).order_by(LegalSignParty.sign_order).all()
    try:
        from app.services.signing_provider_service import signing_provider_service
        dispatched = signing_provider_service.create_and_send(
            request_id=req.id,
            contract_version_id=req.contract_version_id,
            parties=[{"name": party.name, "phone_masked": party.phone_masked, "sign_order": party.sign_order} for party in parties],
            deadline_at=req.deadline_at,
        )
    except ValueError as exc:
        raise HTTPException(503, detail=str(exc)) from exc
    duplicate = db.query(LegalSignRequest).filter(
        LegalSignRequest.provider == req.provider,
        LegalSignRequest.provider_request_id == dispatched.provider_request_id,
        LegalSignRequest.id != req.id,
    ).first()
    if duplicate:
        raise HTTPException(409, detail="服务商签署单已关联到其他请求")
    req.provider_request_id = dispatched.provider_request_id
    req.status = "sent"
    req.sent_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)
    security_audit_service.write_event(
        event_type="sign_callback", actor_type="user", result="success",
        organization_id=req.organization_id, actor_id=str(current_user.id),
        target_type="sign_request", target_id=str(req.id),
        detail_json_hash=f"send:provider={req.provider}",
        db=db,
    )
    db.refresh(req)
    return req


class SignCallback(BaseModel):
    event_type: str = Field(..., pattern="^(signed|rejected|expired|sent|viewed|reminded)$")
    provider_event_id: str = Field(..., min_length=1, max_length=128)
    occurred_at: datetime
    provider_request_id: str = Field(..., min_length=1, max_length=128)
    result: str = Field("success", pattern="^(success|failed|pending)$")
    party_provider_sign_id: Optional[str] = Field(None, max_length=128)
    reject_reason: Optional[str] = Field(None, max_length=1000)


def _verify_signing_webhook(provider: str, payload: bytes, signature: Optional[str]) -> None:
    try:
        secrets_by_provider = json.loads(get_settings().SIGNING_WEBHOOK_SECRETS_JSON or "{}")
    except ValueError:
        secrets_by_provider = {}
    secret = secrets_by_provider.get(provider)
    if not secret or not signature:
        raise HTTPException(503, detail="签署回调验签尚未配置")
    expected = hmac.new(str(secret).encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.strip()):
        raise HTTPException(401, detail="签署回调验签失败")


def _archive_signed_document(db: Session, req: LegalSignRequest, event_id: str) -> None:
    version = db.query(LegalContractVersion).filter(
        LegalContractVersion.id == req.contract_version_id
    ).first()
    if not version or not version.source_document_id:
        return
    from app.models.document import Document
    document = db.query(Document).filter(Document.id == version.source_document_id).first()
    if not document:
        return
    try:
        metadata = json.loads(document.metadata_json or "{}")
    except (TypeError, ValueError):
        metadata = {}
    metadata.update({
        "case_id": db.query(LegalContract.case_id).filter(LegalContract.id == req.contract_id).scalar(),
        "signed_contract": True,
        "sign_provider": req.provider,
        "provider_request_id": req.provider_request_id,
        "sign_event_id": event_id,
        "signed_at": datetime.now(timezone.utc).isoformat(),
    })
    document.metadata_json = json.dumps(metadata, ensure_ascii=False)


@router.post("/signing/webhooks/{provider}")
async def sign_callback(
    provider: str,
    request: Request,
    x_signature: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    if provider not in {"fadada", "esigncn", "other"}:
        raise HTTPException(404)
    raw_payload = await request.body()
    _verify_signing_webhook(provider, raw_payload, x_signature)
    try:
        body = SignCallback.model_validate_json(raw_payload)
    except ValueError as exc:
        raise HTTPException(400, detail="签署回调格式无效") from exc
    # 幂等去重
    existing = db.query(LegalSignEvent).filter(
        LegalSignEvent.provider_event_id == body.provider_event_id
    ).first()
    if existing:
        return {"idempotent": True}

    req = db.query(LegalSignRequest).filter(
        LegalSignRequest.provider == provider,
        LegalSignRequest.provider_request_id == body.provider_request_id,
    ).first()
    if not req:
        raise HTTPException(404)

    event = LegalSignEvent(
        sign_request_id=req.id,
        event_type=body.event_type,
        provider_event_id=body.provider_event_id,
        occurred_at=body.occurred_at,
        raw_payload_hash=hashlib.sha256(raw_payload).hexdigest(),
        result=body.result,
    )
    db.add(event)

    from app.services import security_audit_service

    # 异常时序检测：事件发生时间早于该请求已记录的最新事件，或晚于当前时间超过5分钟
    now = datetime.now(timezone.utc)
    occurred_at = body.occurred_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    latest_event = db.query(LegalSignEvent).filter(
        LegalSignEvent.sign_request_id == req.id,
    ).order_by(LegalSignEvent.occurred_at.desc()).first()

    anomalous = False
    if latest_event is not None:
        latest_at = latest_event.occurred_at
        if latest_at.tzinfo is None:
            latest_at = latest_at.replace(tzinfo=timezone.utc)
        if occurred_at < latest_at:
            anomalous = True  # 乱序回调：新事件时间早于已记录的最新事件
    if occurred_at > now + timedelta(minutes=5):
        anomalous = True  # 未来时间戳，视为异常
    if body.event_type in {"signed", "rejected", "expired"} and body.result == "failed":
        anomalous = True  # 服务商标记该终态事件失败，不可伪造成功终态

    terminal_states = {"signed", "rejected", "expired"}
    if anomalous:
        if req.status not in terminal_states:
            req.status = "needs_attention"
        security_audit_service.write_event(
            event_type="sign_callback",
            actor_type="system",
            result="blocked",
            organization_id=req.organization_id,
            target_type="sign_request",
            target_id=str(req.id),
            detail_json_hash=f"anomalous_callback:event={body.event_type};provider_event_id={body.provider_event_id}",
            db=db,
        )
    else:
        # 状态单向流转，终态不可回退
        if req.status not in terminal_states:
            state_map = {
                "signed": "signed",
                "rejected": "rejected",
                "expired": "expired",
                "sent": "pending_sign",
            }
            new_status = state_map.get(body.event_type)
            if new_status:
                req.status = new_status
        if body.event_type == "signed":
            contract = db.query(LegalContract).filter(LegalContract.id == req.contract_id).first()
            if contract:
                contract.status = "signed"
            _archive_signed_document(db, req, body.provider_event_id)
        elif body.event_type == "rejected" and body.party_provider_sign_id:
            party = db.query(LegalSignParty).filter(
                LegalSignParty.sign_request_id == req.id,
                LegalSignParty.provider_sign_id == body.party_provider_sign_id,
            ).first()
            if party:
                party.status = "rejected"
                party.rejected_at = datetime.now(timezone.utc)
                party.reject_reason = body.reject_reason
        security_audit_service.write_event(
            event_type="sign_callback",
            actor_type="system",
            result="success",
            organization_id=req.organization_id,
            target_type="sign_request",
            target_id=str(req.id),
            detail_json_hash=f"event={body.event_type};provider_event_id={body.provider_event_id}",
            db=db,
        )

    db.commit()
    oplog_service.log(module="legal_signing", action=f"sign_{body.event_type}", db=db,
                      user_id=req.initiated_by, target_type="sign_request", target_id=req.id,
                      detail=f"provider={provider}; provider_event_id={body.provider_event_id}; anomalous={anomalous}")
    return {"processed": True, "anomalous": anomalous}


@router.get("/sign-requests/{request_id}")
def get_sign_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    req = db.query(LegalSignRequest).filter(LegalSignRequest.id == request_id).first()
    if not req:
        raise HTTPException(404)

    # 与 list_sign_requests 一致：强制合同/案件级访问控制（严格案件非成员返回404）
    verify_contract_access(req.contract_id, current_user, db)

    parties = db.query(LegalSignParty).filter(LegalSignParty.sign_request_id == request_id).all()
    return {"request": req, "parties": parties}


@router.get("/sign-requests/{request_id}/evidence")
def get_sign_evidence(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """签署证据包：事件流水 + 归档状态。独立于签署请求详情单独审计访问。"""
    req = db.query(LegalSignRequest).filter(LegalSignRequest.id == request_id).first()
    if not req:
        raise HTTPException(404)

    # 与 list_sign_requests 一致：强制合同/案件级访问控制
    verify_contract_access(req.contract_id, current_user, db)

    events = db.query(LegalSignEvent).filter(
        LegalSignEvent.sign_request_id == request_id
    ).order_by(LegalSignEvent.occurred_at).all()
    parties = db.query(LegalSignParty).filter(LegalSignParty.sign_request_id == request_id).all()

    version = db.query(LegalContractVersion).filter(
        LegalContractVersion.id == req.contract_version_id
    ).first()
    archived = False
    if version and version.source_document_id:
        from app.models.document import Document
        document = db.query(Document).filter(Document.id == version.source_document_id).first()
        if document:
            try:
                metadata = json.loads(document.metadata_json or "{}")
            except (TypeError, ValueError):
                metadata = {}
            archived = bool(metadata.get("signed_contract") and metadata.get("sign_event_id"))

    from app.services import security_audit_service
    security_audit_service.write_event(
        event_type="admin_view", actor_type="user", result="success",
        organization_id=req.organization_id, actor_id=str(current_user.id),
        target_type="sign_request", target_id=str(request_id),
        detail_json_hash="view_evidence", db=db,
    )

    return {
        "request_id": req.id,
        "status": req.status,
        "provider": req.provider,
        "archived": archived,
        "events": [
            {
                "id": e.id, "event_type": e.event_type, "occurred_at": e.occurred_at,
                "result": e.result, "processed_at": e.processed_at,
            }
            for e in events
        ],
        "parties": [
            {"id": p.id, "name": p.name, "sign_order": p.sign_order, "status": p.status,
             "signed_at": p.signed_at, "rejected_at": p.rejected_at}
            for p in parties
        ],
    }


# ── Review Policies ───────────────────────────────────────────────────────────

class ReviewPolicyCreate(BaseModel):
    name: str = Field(..., max_length=128)
    party_role: str = Field("unknown", pattern="^(party_a|party_b|platform|unknown)$")
    contract_type: Optional[str] = None
    scenario: Optional[str] = None
    risk_preference: str = Field("standard", pattern="^(strict|standard|lenient)$")
    required_clauses_json: Optional[str] = None
    focus_points: Optional[str] = Field(None, max_length=2000)


@router.get("/orgs/{org_id}/review-policies")
def list_review_policies(
    org_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_member_access(org_id, current_user.id, db)
    return db.query(LegalReviewPolicy).filter(
        LegalReviewPolicy.organization_id == org_id,
        LegalReviewPolicy.is_active == 1,
    ).all()


@router.post("/orgs/{org_id}/review-policies")
def create_review_policy(
    org_id: int,
    body: ReviewPolicyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verify_org_role_access(org_id, current_user.id, LegalMemberRole.editor, db)
    import json
    policy = LegalReviewPolicy(
        organization_id=org_id,
        name=body.name,
        party_role=body.party_role,
        contract_type=body.contract_type,
        scenario=body.scenario,
        risk_preference=body.risk_preference,
        required_clauses_json=body.required_clauses_json,
        focus_points=body.focus_points,
        created_by=current_user.id,
    )
    db.add(policy)
    db.flush()

    # 保存初始版本快照
    snapshot = {
        "name": body.name, "party_role": body.party_role,
        "contract_type": body.contract_type, "risk_preference": body.risk_preference,
    }
    db.add(LegalReviewPolicyVersion(
        policy_id=policy.id,
        version=1,
        name=body.name,
        config_snapshot=json.dumps(snapshot, ensure_ascii=False),
        created_by=current_user.id,
    ))

    db.commit()
    db.refresh(policy)
    return policy
