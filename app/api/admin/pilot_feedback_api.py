"""#72/试点退出问卷与 NPS 回收 API（pilot-success-playbook §5）"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.api_response import paginated_payload
from app.core.auth import get_current_user, require_admin_user
from app.core.database import get_db
from app.models.feedback import ExitSurvey, NpsResponse
from app.models.org import OrganizationMember
from app.models.user import User

router = APIRouter()

_TRUST_OPTIONS = {"credible", "indifferent", "not_trusted"}
_CITATION_OPTIONS = {"frequent", "occasional", "never"}
_NEXT_STEP_OPTIONS = {"clear", "indifferent", "missing"}
_PAY_OPTIONS = {"renew", "try_more", "expensive", "wont"}


class ExitSurveyRequest(BaseModel):
    nps_score: Optional[int] = Field(None, ge=0, le=10)
    trust_confidence: Optional[str] = None
    trust_citations: Optional[str] = None
    trust_next_steps: Optional[str] = None
    value_ranking: Optional[str] = None
    review_wish: Optional[str] = None
    pain_point: Optional[str] = None
    pay_intent: Optional[str] = None
    feature_requests: Optional[str] = None
    summary_feedback: Optional[str] = None

    @field_validator("trust_confidence")
    @classmethod
    def _v_confidence(cls, v):
        if v is not None and v not in _TRUST_OPTIONS:
            raise ValueError(f"trust_confidence must be one of {sorted(_TRUST_OPTIONS)}")
        return v

    @field_validator("trust_citations")
    @classmethod
    def _v_citations(cls, v):
        if v is not None and v not in _CITATION_OPTIONS:
            raise ValueError(f"trust_citations must be one of {sorted(_CITATION_OPTIONS)}")
        return v

    @field_validator("trust_next_steps")
    @classmethod
    def _v_next_steps(cls, v):
        if v is not None and v not in _NEXT_STEP_OPTIONS:
            raise ValueError(f"trust_next_steps must be one of {sorted(_NEXT_STEP_OPTIONS)}")
        return v

    @field_validator("pay_intent")
    @classmethod
    def _v_pay(cls, v):
        if v is not None and v not in _PAY_OPTIONS:
            raise ValueError(f"pay_intent must be one of {sorted(_PAY_OPTIONS)}")
        return v


class NpsRequest(BaseModel):
    score: int = Field(..., ge=0, le=10)


def _org_id_for(user_id: int, db: Session) -> Optional[int]:
    member = (
        db.query(OrganizationMember)
        .filter(OrganizationMember.user_id == user_id)
        .order_by(OrganizationMember.organization_id.asc())
        .first()
    )
    return member.organization_id if member else None


@router.post("/exit-survey")
def submit_exit_survey(
    payload: ExitSurveyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    survey = ExitSurvey(
        user_id=current_user.id,
        org_id=_org_id_for(current_user.id, db),
        nps_score=payload.nps_score,
        trust_confidence=payload.trust_confidence,
        trust_citations=payload.trust_citations,
        trust_next_steps=payload.trust_next_steps,
        value_ranking=payload.value_ranking,
        review_wish=payload.review_wish,
        pain_point=payload.pain_point,
        pay_intent=payload.pay_intent,
        feature_requests=payload.feature_requests,
        summary_feedback=payload.summary_feedback,
    )
    db.add(survey)
    db.commit()
    db.refresh(survey)
    return {"survey_id": survey.id}


@router.post("/nps")
def submit_nps(
    payload: NpsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    resp = NpsResponse(
        user_id=current_user.id,
        org_id=_org_id_for(current_user.id, db),
        score=payload.score,
        source="in_app",
    )
    db.add(resp)
    db.commit()
    db.refresh(resp)
    return {"response_id": resp.id}


@router.get("/admin/surveys")
def list_surveys(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    surveys = (
        db.query(ExitSurvey)
        .order_by(ExitSurvey.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    total = db.query(ExitSurvey).count()
    items = [
        {
            "id": s.id,
            "user_id": s.user_id,
            "org_id": s.org_id,
            "nps_score": s.nps_score,
            "trust_confidence": s.trust_confidence,
            "trust_citations": s.trust_citations,
            "trust_next_steps": s.trust_next_steps,
            "value_ranking": s.value_ranking,
            "review_wish": s.review_wish,
            "pain_point": s.pain_point,
            "pay_intent": s.pay_intent,
            "feature_requests": s.feature_requests,
            "summary_feedback": s.summary_feedback,
            "created_at": s.created_at,
        }
        for s in surveys
    ]
    return paginated_payload(items, total=total, page=page, page_size=page_size)


@router.get("/admin/nps-stats")
def nps_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    rows = db.query(NpsResponse).all()
    total = len(rows)
    promoters = sum(1 for r in rows if r.score >= 9)
    detractors = sum(1 for r in rows if r.score <= 6)
    nps = round((promoters - detractors) / total * 100, 1) if total else None
    return {
        "total": total,
        "promoters": promoters,
        "detractors": detractors,
        "passives": total - promoters - detractors,
        "nps": nps,
        "by_source": {
            src: sum(1 for r in rows if r.source == src)
            for src in {r.source for r in rows}
        },
    }
