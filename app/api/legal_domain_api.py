"""P1 法律业务统一模型 API：案件域聚合 / 结构化风险项 / 发布门禁。

端点（均挂 /api/legal）：
  GET  /cases/{case_id}/domain                               案件事实/证据/主张/引用/风险项聚合
  GET  /contract-reviews/{item_id}/risk-items               结构化风险项列表
  POST /contract-reviews/{item_id}/risk-items/{risk_id}/action  律师处理风险项（accept/mitigate/dismiss）
  POST /contract-reviews/{item_id}/publish                  发布合同审查结论（审核门禁）
  POST /drafts/{item_id}/mark-final                         文书定稿（审核门禁）
  GET  /consultations/{item_id}/claims                      主张追溯
  GET  /contract-reviews/{item_id}/claims                   主张追溯
  GET  /drafts/{item_id}/claims                             主张追溯

权限：案件级端点走 verify_case_access；工作台记录允许本人或 admin/dept_admin 查看；
风险项处理与审核相关操作仅 admin/dept_admin。发布门禁由服务端强制，前端隐藏按钮不作为安全边界。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.api_response import api_error
from app.core.auth import get_current_user, verify_case_access
from app.core.database import get_db
from app.models.legal import ContractReview, LegalConsultation, LegalDraft, LegalReviewAction
from app.models.legal_domain import ContractRiskItem
from app.models.user import User
from app.services.audit_log_service import AuditLogService
from app.services.legal_domain_service import (
    get_case_domain,
    get_claims_for_target,
    get_risk_items,
    legal_domain_service,
)
from app.services.legal_workspace_service import serialize_workspace_row

router = APIRouter()
audit = AuditLogService()


class RiskActionIn(BaseModel):
    action: str = Field(description="accept / mitigate / dismiss")
    note: str | None = Field(default=None, max_length=2000)


def _load_owned_row(db: Session, user: User, model, item_id: int, kind: str):
    """按本人或审核角色加载工作台记录，并校验关联案件访问权限。"""
    row = db.query(model).filter(model.id == item_id).first()
    if not row:
        raise api_error(404, "记录不存在", code=f"LEGAL_{kind.upper()}_NOT_FOUND")
    if row.user_id != user.id and user.role not in {"admin", "dept_admin"}:
        raise api_error(403, "无权访问该记录", code="LEGAL_ACCESS_FORBIDDEN")
    if row.case_id:
        try:
            verify_case_access(row.case_id, user.id, db)
        except Exception:
            raise api_error(404, "记录不存在", code=f"LEGAL_{kind.upper()}_NOT_FOUND")
    return row


@router.get("/cases/{case_id}/domain")
def case_domain(case_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """案件级法律业务数据聚合（facts/evidences/claims/references/risk_items）。"""
    try:
        verify_case_access(case_id, current_user.id, db)
    except Exception:
        raise api_error(404, "案件不存在或无权访问", code="CASE_NOT_FOUND")
    return get_case_domain(db, case_id)


@router.get("/contract-reviews/{item_id}/risk-items")
def list_risk_items(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _load_owned_row(db, current_user, ContractReview, item_id, "contract_review")
    return get_risk_items(db, item_id)


@router.post("/contract-reviews/{item_id}/risk-items/{risk_id}/action")
def handle_risk_item(item_id: int, risk_id: int, req: RiskActionIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """律师处理风险项：accept / mitigate / dismiss。仅审核角色可执行。"""
    if current_user.role not in {"admin", "dept_admin"}:
        raise api_error(403, "仅审核律师或管理员可处理风险项", code="LEGAL_RISK_REVIEW_FORBIDDEN")
    _load_owned_row(db, current_user, ContractReview, item_id, "contract_review")
    risk = db.get(ContractRiskItem, risk_id)
    if not risk or risk.review_id != item_id:
        raise api_error(404, "风险项不存在", code="RISK_ITEM_NOT_FOUND")
    try:
        result = legal_domain_service.update_risk_item_status(db, current_user, risk_id, req.action, req.note)
    except LookupError:
        raise api_error(404, "风险项不存在", code="RISK_ITEM_NOT_FOUND")
    except ValueError as exc:
        raise api_error(400, str(exc), code="RISK_ITEM_ACTION_INVALID")
    audit.log(
        db, current_user, f"legal_risk_item_{req.action}", target_type="contract_review",
        target_id=item_id, detail=f"risk_item={risk_id}",
    )
    return result


def _finalize(db: Session, user: User, *, target_type: str, target_id: int, model, kind: str):
    row = _load_owned_row(db, user, model, target_id, kind)
    verdict = legal_domain_service.assert_publishable(db, user, target_type, target_id)
    if not verdict["ok"]:
        raise api_error(409, "；".join(verdict["reasons"]), code="LEGAL_NOT_PUBLISHABLE")
    if getattr(row, "is_final", 0):
        return serialize_workspace_row(row)
    row.is_final = 1
    db.add(LegalReviewAction(
        reviewer_id=user.id, target_type=target_type, target_id=target_id, action="finalize",
        from_status=row.status, to_status=row.status, target_version=getattr(row, "version", None),
    ))
    db.commit()
    db.refresh(row)
    audit.log(
        db, user, f"legal_{target_type}_finalize", target_type=target_type,
        target_id=target_id, detail=f"version={getattr(row, 'version', None)}",
    )
    return serialize_workspace_row(row)


@router.post("/contract-reviews/{item_id}/publish")
def publish_contract_review(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """发布合同审查结论：须当前版本已审核通过且无未处理高/严重风险项。"""
    return _finalize(db, current_user, target_type="contract_review", target_id=item_id,
                     model=ContractReview, kind="contract_review")


@router.post("/drafts/{item_id}/mark-final")
def mark_draft_final(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """文书定稿：须当前版本已审核通过。"""
    return _finalize(db, current_user, target_type="draft", target_id=item_id,
                     model=LegalDraft, kind="draft")


@router.get("/consultations/{item_id}/claims")
def consultation_claims(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _load_owned_row(db, current_user, LegalConsultation, item_id, "consultation")
    return get_claims_for_target(db, "consultation", item_id)


@router.get("/contract-reviews/{item_id}/claims")
def review_claims(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _load_owned_row(db, current_user, ContractReview, item_id, "contract_review")
    return get_claims_for_target(db, "contract_review", item_id)


@router.get("/drafts/{item_id}/claims")
def draft_claims(item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _load_owned_row(db, current_user, LegalDraft, item_id, "draft")
    return get_claims_for_target(db, "draft", item_id)
