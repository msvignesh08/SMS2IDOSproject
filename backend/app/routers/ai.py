"""THE AI PRODUCTION ENGINEER API (Constitution S.6) — strictly READ ONLY.
It analyzes, explains, recommends, predicts, teaches. It never writes
production data; its only side effect is posting AI alert notifications."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from ..core.database import get_db
from .. import models as M
from ..engines.insights import compute_insights, push_ai_alerts, answer, MODES
from .auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/insights")
def insights(user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    ins = compute_insights(db)
    push_ai_alerts(db, ins)
    db.commit()
    return {"persona": "AI Production Engineer", "modes": MODES, "insights": ins}


class AskIn(BaseModel):
    question: str
    mode: str = "production_engineer"


@router.post("/ask")
def ask(body: AskIn, user: M.User = Depends(get_current_user),
        db: DBSession = Depends(get_db)):
    return answer(db, body.question, body.mode)
