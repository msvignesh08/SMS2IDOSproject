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
from ..engines import ai_brain
from .auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/insights")
def insights(user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    ins = compute_insights(db)
    push_ai_alerts(db, ins)
    db.commit()
    return {"persona": "AI Production Engineer", "modes": MODES, "insights": ins,
            # Which brain is answering. The screen says so plainly rather than
            # letting an operator guess whether they got reasoning or a
            # keyword match.
            "brain": ai_brain.config(), "brain_modes": ai_brain.MODES}


class AskIn(BaseModel):
    question: str
    mode: str = "production_engineer"
    history: list = []      # prior turns, so follow-ups keep their thread


@router.post("/ask")
def ask(body: AskIn, user: M.User = Depends(get_current_user),
        db: DBSession = Depends(get_db)):
    """Answer a question — reasoning when it is available, rules when it is not.

    THE FALLBACK IS THE POINT. SMS-2 runs on plant intranets with no outbound
    internet, so the deterministic engine stays the floor: if no key is set,
    the network is down, or the API errors, the operator still gets the same
    answer they got before this existed. They are told which one they got.
    """
    r = ai_brain.ask(db, body.question, body.mode, body.history)
    if r.get("ok"):
        return {"ok": True, "answer": r["answer"], "source": "claude",
                "model": r.get("model"), "tools_used": r.get("tools_used", []),
                "usage": r.get("usage")}
    base = answer(db, body.question, body.mode)
    base["source"] = "engine"
    if r.get("reason") == "error":
        base["degraded"] = r.get("detail", "")
    return base
