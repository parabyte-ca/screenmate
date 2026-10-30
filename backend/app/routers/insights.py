"""/api/insights — savings, library make-up, encoder speed and what's left to gain."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services import insights

router = APIRouter(tags=["insights"])


@router.get("/insights")
def get_insights(days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    return insights.compute(db, days)
