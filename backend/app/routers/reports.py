"""IRAC (Section 9): reports + universal search + analytics + knowledge notes."""
from __future__ import annotations
import os
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_, func
from sqlalchemy.orm import Session as DBSession
from ..core.database import get_db
from ..core import plant_time
from .. import models as M
from ..engines import lifecycle as L
from ..engines.reports import (daily_report_data, heat_report_data,
                               export_daily_xlsx, export_heat_xlsx, REPORT_DIR,
                               export_range_xlsx, range_report_data)
from ..engines.stage_config import STAGE_DEFS
from .auth import get_current_user, require_perm

router = APIRouter(prefix="/api", tags=["reports"])


@router.get("/reports/daily")
def daily(plant_day: str | None = None,
          user: M.User = Depends(require_perm("reports.view")),
          db: DBSession = Depends(get_db)):
    pd = date.fromisoformat(plant_day) if plant_day else plant_time.now_moment().plant_day
    return daily_report_data(db, pd)


@router.get("/reports/heat/{heat_no}")
def heat_report(heat_no: str, user: M.User = Depends(require_perm("reports.view")),
                db: DBSession = Depends(get_db)):
    h = db.query(M.Heat).filter_by(heat_no=heat_no.upper()).first()
    if not h:
        raise HTTPException(404, "Heat not found")
    return heat_report_data(db, h)


@router.get("/reports/daily/xlsx")
def daily_xlsx(plant_day: str | None = None,
               user: M.User = Depends(require_perm("reports.export")),
               db: DBSession = Depends(get_db)):
    pd = date.fromisoformat(plant_day) if plant_day else plant_time.now_moment().plant_day
    path = export_daily_xlsx(db, pd, user.name)
    L.audit(db, user, "report", "daily_xlsx_export", detail=pd.isoformat())
    db.commit()
    return FileResponse(path, filename=os.path.basename(path),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/reports/heat/{heat_no}/xlsx")
def heat_xlsx(heat_no: str, user: M.User = Depends(require_perm("reports.export")),
              db: DBSession = Depends(get_db)):
    h = db.query(M.Heat).filter_by(heat_no=heat_no.upper()).first()
    if not h:
        raise HTTPException(404, "Heat not found")
    path = export_heat_xlsx(db, h, user.name)
    L.audit(db, user, "report", "heat_xlsx_export", heat_no=h.heat_no)
    db.commit()
    return FileResponse(path, filename=os.path.basename(path),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/reports/range")
def range_report(date_from: str, date_to: str,
                 user: M.User = Depends(get_current_user),
                 db: DBSession = Depends(get_db)):
    """Production across a span of plant days, on screen."""
    try:
        return range_report_data(db, date.fromisoformat(date_from),
                                 date.fromisoformat(date_to))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/reports/range/xlsx")
def range_xlsx(date_from: str, date_to: str,
               user: M.User = Depends(require_perm("reports.export")),
               db: DBSession = Depends(get_db)):
    """The same span as a workbook — summary, per day, every heat, delays."""
    try:
        path = export_range_xlsx(db, date.fromisoformat(date_from),
                                 date.fromisoformat(date_to), user.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    L.audit(db, user, "report", "range_xlsx_export",
            detail=f"{date_from} to {date_to}")
    db.commit()
    return FileResponse(path, filename=os.path.basename(path),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/reports/archive")
def report_library(user: M.User = Depends(require_perm("reports.view")),
                   db: DBSession = Depends(get_db)):
    """Report Library (9.21): every generated report is a preserved asset."""
    rows = (db.query(M.ReportArchive)
            .order_by(M.ReportArchive.at.desc()).limit(200).all())
    return [{"id": r.id, "at": r.at.isoformat(sep=" "), "kind": r.kind,
             "ref": r.ref, "fmt": r.fmt, "filename": r.filename,
             "generated_by": r.generated_by} for r in rows]


@router.get("/reports/archive/{rid}/download")
def download_archived(rid: int, user: M.User = Depends(require_perm("reports.view")),
                      db: DBSession = Depends(get_db)):
    r = db.get(M.ReportArchive, rid)
    if not r:
        raise HTTPException(404, "Report not found")
    path = os.path.join(REPORT_DIR, r.filename)
    if not os.path.exists(path):
        raise HTTPException(410, "Report file missing from archive volume")
    return FileResponse(path, filename=r.filename)


# ─────────── UNIVERSAL SEARCH (9.22 / 4.7) ───────────

@router.get("/search")
def search(q: str = "", status: str | None = None, shift: str | None = None,
           grade: str | None = None, route: str | None = None,
           plant_day: str | None = None, limit: int = 50,
           user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    query = db.query(M.Heat).filter(M.Heat.archived == False)  # noqa: E712
    if status:
        query = query.filter(M.Heat.status == status)
    if shift:
        query = query.filter(M.Heat.shift == shift)
    if route:
        query = query.filter(M.Heat.route == route)
    if plant_day:
        query = query.filter(M.Heat.plant_day == date.fromisoformat(plant_day))
    if grade:
        query = query.join(M.SteelGrade).filter(M.SteelGrade.code.ilike(f"%{grade}%"))
    heats = query.order_by(M.Heat.created_at.desc()).limit(500).all()

    ql = q.strip().lower()
    results = []
    for h in heats:
        hay = [h.heat_no.lower(), (h.grade.code.lower() if h.grade else ""),
               h.status, h.shift.lower(), (h.route or "").lower(),
               h.shift_engineer.lower(), h.plant_day.isoformat()]
        matched_in = "heat"
        ok = (not ql) or any(ql in s for s in hay)
        detail = ""
        if not ok and ql:
            # deep search: remarks, delay reasons, chemistry stages, personnel
            evs = db.query(M.HeatEvent).filter(M.HeatEvent.heat_id == h.id,
                                               M.HeatEvent.detail.ilike(f"%{ql}%")).first()
            if evs:
                ok, matched_in, detail = True, "timeline", evs.detail[:120]
            else:
                dl = (db.query(M.Delay).join(M.DelayReason)
                      .filter(M.Delay.heat_id == h.id,
                              or_(M.DelayReason.reason.ilike(f"%{ql}%"),
                                  M.Delay.remarks.ilike(f"%{ql}%"))).first())
                if dl:
                    ok, matched_in = True, "delay"
                    detail = dl.reason.reason if dl.reason else dl.remarks
        if ok:
            results.append({"heat_no": h.heat_no,
                            "grade": h.grade.code if h.grade else "",
                            "status": h.status, "route": h.route,
                            "plant_day": h.plant_day.isoformat(), "shift": h.shift,
                            "shift_engineer": h.shift_engineer,
                            "is_training": h.is_training,
                            "matched_in": matched_in, "detail": detail})
        if len(results) >= limit:
            break
    return {"query": q, "count": len(results), "results": results}


# ─────────── ANALYTICS (4.9 / 9.26) ───────────

@router.get("/analytics")
def analytics(days: int = 14, user: M.User = Depends(require_perm("reports.view")),
              db: DBSession = Depends(get_db)):
    today = plant_time.now_moment().plant_day
    start = today - timedelta(days=days - 1)
    heats = (db.query(M.Heat)
             .filter(M.Heat.plant_day >= start, M.Heat.is_training == False,   # noqa
                     M.Heat.archived == False).all())                          # noqa
    trend = {}
    for i in range(days):
        d = (start + timedelta(days=i)).isoformat()
        trend[d] = {"heats": 0, "billet_t": 0.0, "energy_kwh": 0.0, "delay_min": 0.0}
    for h in heats:
        k = h.plant_day.isoformat()
        trend[k]["heats"] += 1
        for r in db.query(M.StageRecord).filter_by(heat_id=h.id):
            d = r.data or {}
            if r.stage == "caster":
                trend[k]["billet_t"] += float(d.get("billet_weight") or 0)
            if r.stage == "eaf":
                trend[k]["energy_kwh"] += float(d.get("energy_kwh") or 0)
        for dl in db.query(M.Delay).filter_by(heat_id=h.id, superseded=False):
            trend[k]["delay_min"] += dl.duration_min or 0
    # delay pareto
    pareto = (db.query(M.DelayReason.category, func.sum(M.Delay.duration_min))
              .join(M.Delay, M.Delay.reason_id == M.DelayReason.id)
              .join(M.Heat, M.Delay.heat_id == M.Heat.id)
              .filter(M.Heat.plant_day >= start, M.Delay.superseded == False)  # noqa
              .group_by(M.DelayReason.category)
              .order_by(func.sum(M.Delay.duration_min).desc()).all())
    # shift comparison
    shifts = {}
    for h in heats:
        s = shifts.setdefault(h.shift, {"heats": 0, "locked": 0})
        s["heats"] += 1
        if h.status == "locked":
            s["locked"] += 1
    # route usage
    routes = {}
    for h in heats:
        routes[h.route or "undecided"] = routes.get(h.route or "undecided", 0) + 1
    return {"window": [start.isoformat(), today.isoformat()],
            "trend": [{"plant_day": k, **v} for k, v in trend.items()],
            "delay_pareto": [{"category": c, "minutes": round(m or 0, 0)} for c, m in pareto],
            "shift_comparison": shifts, "route_usage": routes}


# ─────────── KNOWLEDGE ENGINE (Sections 12–13) ───────────

class NoteIn(BaseModel):
    heat_no: str | None = None
    grade_code: str | None = None
    topic: str = ""
    body: str
    tags: str = ""


@router.post("/knowledge")
def add_note(body: NoteIn, user: M.User = Depends(require_perm("knowledge.write")),
             db: DBSession = Depends(get_db)):
    db.add(M.KnowledgeNote(author=user.name, heat_no=body.heat_no,
                           grade_code=body.grade_code, topic=body.topic,
                           body=body.body, tags=body.tags))
    L.audit(db, user, "production", "knowledge_note", body.heat_no, body.topic)
    db.commit()
    return {"ok": True}


@router.get("/knowledge")
def knowledge(q: str = "", grade: str | None = None, heat_no: str | None = None,
              user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    """Organizational Memory (Section 13): searchable engineering knowledge —
    what happened, why, who approved, what was learned — plus derived
    practice statistics from APPROVED heats only (7.6)."""
    query = db.query(M.KnowledgeNote).filter(M.KnowledgeNote.archived == False)  # noqa
    if q:
        like = f"%{q}%"
        query = query.filter(or_(M.KnowledgeNote.body.ilike(like),
                                 M.KnowledgeNote.topic.ilike(like),
                                 M.KnowledgeNote.tags.ilike(like)))
    if grade:
        query = query.filter(M.KnowledgeNote.grade_code == grade)
    if heat_no:
        query = query.filter(M.KnowledgeNote.heat_no == heat_no.upper())
    notes = query.order_by(M.KnowledgeNote.at.desc()).limit(100).all()

    # grade practice intelligence from locked (approved) heats only
    practice = []
    if grade:
        locked = (db.query(M.Heat).join(M.SteelGrade)
                  .filter(M.SteelGrade.code == grade, M.Heat.status == "locked",
                          M.Heat.is_training == False).all())  # noqa
        vd = sum(1 for h in locked if (h.route or "").startswith("VD"))
        practice = [{"fact": f"{len(locked)} approved heats of {grade} in the knowledge base."},
                    {"fact": f"VD route used on {vd}/{len(locked)} approved heats." if locked else
                             "No approved heats yet for this grade."}]
    return {"notes": [{"id": n.id, "at": n.at.isoformat(sep=" "), "author": n.author,
                       "heat_no": n.heat_no, "grade_code": n.grade_code,
                       "topic": n.topic, "body": n.body, "tags": n.tags}
                      for n in notes],
            "practice": practice}
