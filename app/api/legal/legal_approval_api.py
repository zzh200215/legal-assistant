"""法律多级审批链 API — Phase 9 Week 3

端点：
  POST   /api/legal/orgs/{org_id}/approval-chains        创建审批链
  GET    /api/legal/orgs/{org_id}/approval-chains/{id}   获取审批链详情（含步骤）
  POST   /api/legal/approval-chains/{id}/actions         审批人执行通过/退回
  GET    /api/legal/approval-chains/pending              当前用户待处理的审批链
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.legal import LegalApprovalChain, LegalApprovalStep
from app.models.org import OrganizationMember
from app.models.user import User
from app.services.legal.legal_approval_service import legal_approval_service

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class ApproverIn(BaseModel):
    user_id: int
    role: Optional[str] = None


class ChainCreateIn(BaseModel):
    target_type: str = Field(description="contract_review / draft / consultation")
    target_id: int
    chain_type: str = Field(default="serial", description="serial | parallel")
    approvers: list[ApproverIn] = Field(min_length=1)
    timeout_hours: Optional[int] = Field(default=None, ge=1, le=720)


class ApprovalActionIn(BaseModel):
    action: str = Field(description="approve | reject")
    note: Optional[str] = Field(default=None, max_length=2000)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_member(db: Session, user_id: int, org_id: int) -> OrganizationMember:
    m = db.query(OrganizationMember).filter(
        OrganizationMember.organization_id == org_id,
        OrganizationMember.user_id == user_id,
    ).first()
    if not m:
        raise api_error(403, "不是该组织成员", code="NOT_ORG_MEMBER")
    return m


def _serialize_step(s: LegalApprovalStep) -> dict:
    return {
        "id": s.id,
        "chain_id": s.chain_id,
        "step_order": s.step_order,
        "approver_id": s.approver_id,
        "approver_role": s.approver_role,
        "status": s.status,
        "note": s.note,
        "due_at": s.due_at,
        "acted_at": s.acted_at,
    }


def _serialize_chain(chain: LegalApprovalChain, steps: list[LegalApprovalStep]) -> dict:
    return {
        "id": chain.id,
        "organization_id": chain.organization_id,
        "target_type": chain.target_type,
        "target_id": chain.target_id,
        "chain_type": chain.chain_type,
        "status": chain.status,
        "current_step": chain.current_step,
        "timeout_hours": chain.timeout_hours,
        "created_by": chain.created_by,
        "created_at": chain.created_at,
        "updated_at": chain.updated_at,
        "steps": [_serialize_step(s) for s in steps],
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/orgs/{org_id}/approval-chains", status_code=201)
def create_approval_chain(
    org_id: int,
    req: ChainCreateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建审批链（需要是组织成员）。"""
    _require_member(db, current_user.id, org_id)
    try:
        chain = legal_approval_service.create_chain(
            db=db,
            org_id=org_id,
            target_type=req.target_type,
            target_id=req.target_id,
            chain_type=req.chain_type,
            approvers=[a.model_dump() for a in req.approvers],
            timeout_hours=req.timeout_hours,
            created_by=current_user.id,
        )
    except ValueError as e:
        raise api_error(400, str(e), code="INVALID_CHAIN")
    steps = legal_approval_service.get_chain_steps(db=db, chain_id=chain.id)
    return _serialize_chain(chain, steps)


@router.get("/orgs/{org_id}/approval-chains/{chain_id}")
def get_approval_chain(
    org_id: int,
    chain_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取审批链详情（含所有步骤）。"""
    _require_member(db, current_user.id, org_id)
    chain = legal_approval_service.get_chain(db=db, chain_id=chain_id)
    if not chain or chain.organization_id != org_id:
        raise api_error(404, "审批链不存在", code="CHAIN_NOT_FOUND")
    steps = legal_approval_service.get_chain_steps(db=db, chain_id=chain_id)
    return _serialize_chain(chain, steps)


@router.post("/approval-chains/{chain_id}/actions")
def take_approval_action(
    chain_id: int,
    req: ApprovalActionIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """审批人执行通过/退回。"""
    chain = legal_approval_service.get_chain(db=db, chain_id=chain_id)
    if not chain:
        raise api_error(404, "审批链不存在", code="CHAIN_NOT_FOUND")
    try:
        updated_chain = legal_approval_service.take_action(
            db=db,
            chain_id=chain_id,
            approver_id=current_user.id,
            action=req.action,
            note=req.note,
        )
    except ValueError as e:
        raise api_error(400, str(e), code="ACTION_ERROR")
    steps = legal_approval_service.get_chain_steps(db=db, chain_id=chain_id)
    return _serialize_chain(updated_chain, steps)


@router.get("/approval-chains/pending")
def list_pending_for_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户待处理的所有审批链。"""
    chains = legal_approval_service.get_pending_for_user(db=db, user_id=current_user.id)
    result = []
    for chain in chains:
        steps = legal_approval_service.get_chain_steps(db=db, chain_id=chain.id)
        result.append(_serialize_chain(chain, steps))
    return result
