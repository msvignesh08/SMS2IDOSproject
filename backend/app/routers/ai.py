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
from ..engines import ai_brain, ai_local
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
            "brain": {"local": ai_local.config(), "cloud": ai_brain.config()},
            "brain_modes": ai_brain.MODES}


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
    # LOCAL FIRST. A model on the plant's own server keeps the question and
    # the heat data behind it inside the plant, which is the whole reason the
    # local path exists — so it is preferred over the cloud even though the
    # cloud reasons better. A site that wants the stronger model simply does
    # not set SMS2_LOCAL_LLM_URL.
    degraded = ""
    for engine in (ai_local, ai_brain):
        r = engine.ask(db, body.question, body.mode, body.history)
        if r.get("ok"):
            return {"ok": True, "answer": r["answer"], "source": r["source"],
                    "model": r.get("model"), "tools_used": r.get("tools_used", []),
                    "usage": r.get("usage")}
        if r.get("reason") == "error" and not degraded:
            degraded = r.get("detail", "")
    base = answer(db, body.question, body.mode)
    base["source"] = "engine"
    if degraded:
        base["degraded"] = degraded
    return base


@router.get("/local-status")
def local_status(user: M.User = Depends(get_current_user)):
    """Is the plant's own model server up, and what does it hold?

    Answerable without spending a minute of CPU on an actual question.
    """
    return {"config": ai_local.config(), "ping": ai_local.ping()}
