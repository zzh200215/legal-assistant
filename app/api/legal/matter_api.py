"""Matter-centric read APIs used by the legal workbench."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.legal.matter_service import matter_service

router = APIRouter()


@router.get("/matters/{case_id}/summary")
def matter_summary(case_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return matter_service.summary(db, case_id=case_id, user_id=current_user.id)


@router.get("/matters/{case_id}/artifacts")
def matter_artifacts(
    case_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"case_id": case_id, "items": matter_service.artifacts(db, case_id=case_id, user_id=current_user.id, limit=limit)}


@router.get("/matters/{case_id}/activity")
def matter_activity(
    case_id: int,
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"case_id": case_id, "items": matter_service.activity(db, case_id=case_id, user_id=current_user.id, limit=limit)}
