"""Production API: heats, stage framework, chemistry, materials, delays,
route decision, validation, submission, approvals, timeline & Gantt."""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from ..core.database import get_db
from ..core import plant_time
from .. import models as M
from ..engines import lifecycle as L
from ..engines.stage_config import STAGE_DEFS, required_stages, stage_def_for
from ..engines.validation import validate_stage, auto_calculations
from ..engines.calculator import alloy_requirements, flux_recommendation, wire_recommendation
from .auth import get_current_user, require_perm

router = APIRouter(prefix="/api", tags=["production"])


def _heat(db, heat_no) -> M.Heat:
    h = db.query(M.Heat).filter_by(heat_no=heat_no.upper()).first()
    if not h:
        raise HTTPException(404, f"Heat {heat_no} not found")
    return h


def _guard_editable(heat: M.Heat, user: M.User):
    if heat.status == "locked" and user.role.name != "Administrator":
        raise HTTPException(423, "Heat is locked and read-only (6H.11). Administrator unlock is required for corrections.")
    if heat.aborted:
        raise HTTPException(423, f"Heat is aborted: {heat.abort_reason}")


def _guard_stage_access(stage: str, user: M.User):
    allowed = STAGE_DEFS[stage]["authorized"]
    if user.role.name not in allowed and user.role.name != "Administrator":
        raise HTTPException(403, f"{STAGE_DEFS[stage]['title']} entry is restricted to: {', '.join(allowed)}")


# ─────────── Heat creation & listing ───────────

class HeatCreate(BaseModel):
    grade_id: int | None = None
    grade_code: str | None = None      # GRADE ENTRY SYSTEM: any grade, manual entry
    heat_no: str | None = None
    shift_engineer: str = ""
    is_training: bool = False
    # WHICH FURNACE MELTS THIS HEAT. The plant runs two primary units; the
    # choice drives the heat-number series (E… / F…) and the route.
    entry_stage: str = "eaf"           # eaf|zpf


@router.post("/heats")
def create_heat(body: HeatCreate, user: M.User = Depends(require_perm("heats.create")),
                db: DBSession = Depends(get_db)):
    try:
        gid = body.grade_id
        if gid is None:
            code = (body.grade_code or "").strip()
            if not code:
                raise HTTPException(400, "Provide a steel grade (any grade — new grades are accepted).")
            g = (db.query(M.SteelGrade)
                 .filter(M.SteelGrade.code.ilike(code)).first())
            if not g:
                # GRADE ENTRY SYSTEM: engineers create grades by using them —
                # no software update, no dropdown lock. Specs can be added later
                # in Admin → Masters; chemistry checks apply once specs exist.
                g = M.SteelGrade(code=code)
                db.add(g); db.flush()
                L.audit(db, user, "production", "grade_created", detail=f"New grade '{code}' created at heat entry")
                L.notify(db, f"New steel grade registered: {code}",
                         f"Created by {user.name} during heat entry. Add chemistry specs in Admin → Masters to enable grade-window validation.",
                         kind="admin", audience_role="Administrator")
            gid = g.id
        h = L.create_heat(db, user, gid, body.heat_no,
                          body.shift_engineer or user.name, body.is_training,
                          body.entry_stage)
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(400, str(e))
    return heat_payload(db, h)


@router.get("/heats")
def list_heats(status: str | None = None, active: bool = False, training: bool | None = None,
               limit: int = 100, user: M.User = Depends(get_current_user),
               db: DBSession = Depends(get_db)):
    q = db.query(M.Heat).filter(M.Heat.archived == False)  # noqa: E712
    if status:
        q = q.filter(M.Heat.status == status)
    if active:
        q = q.filter(M.Heat.status.notin_(["locked", "aborted"]))
    if training is not None:
        q = q.filter(M.Heat.is_training == training)
    heats = q.order_by(M.Heat.created_at.desc()).limit(limit).all()
    return [heat_summary(db, h) for h in heats]


def heat_summary(db, h: M.Heat):
    gates = L.stage_gates(db, h)
    return {"heat_no": h.heat_no, "grade": h.grade.code if h.grade else None,
            "status": h.status, "route": h.route,
            "entry_stage": h.entry_stage or "eaf",
            "route_refining": h.route_refining,
            "plant_day": h.plant_day.isoformat(), "shift": h.shift,
            "shift_engineer": h.shift_engineer,
            "is_training": h.is_training, "is_imported": bool(h.is_imported),
            "aborted": h.aborted,
            "created_at": h.created_at.isoformat(sep=" "),
            "gates": gates}


def heat_payload(db, h: M.Heat):
    p = heat_summary(db, h)
    specs = L.grade_specs_map(db, h.grade_id)
    p["grade_specs"] = specs
    p["approvals"] = [{"level": a.level, "role": a.role_name, "user": a.user_name,
                       "at": a.at.isoformat(sep=" "), "decision": a.decision}
                      for a in db.query(M.Approval).filter_by(heat_id=h.id, scope="final")]
    p["required_stages"] = L.heat_required_stages(db, h)
    return p


@router.get("/heats/{heat_no}")
def get_heat(heat_no: str, user: M.User = Depends(get_current_user),
             db: DBSession = Depends(get_db)):
    return heat_payload(db, _heat(db, heat_no))


class AbortIn(BaseModel):
    reason: str


@router.post("/heats/{heat_no}/abort")
def abort(heat_no: str, body: AbortIn,
          user: M.User = Depends(require_perm("heats.abort")),
          db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    try:
        L.abort_heat(db, h, user, body.reason)
        db.commit()
    except ValueError as e:
        db.rollback(); raise HTTPException(400, str(e))
    return {"ok": True}


# ─────────── Stage framework ───────────

@router.get("/stage-defs")
def stage_defs(user: M.User = Depends(get_current_user)):
    return {"stages": STAGE_DEFS, "sequence": L.STAGE_SEQUENCE}


@router.get("/heats/{heat_no}/stages/{stage}")
def get_stage(heat_no: str, stage: str, user: M.User = Depends(get_current_user),
              db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    if stage not in STAGE_DEFS:
        raise HTTPException(404, "Unknown stage")
    rec = db.query(M.StageRecord).filter_by(heat_id=h.id, stage=stage).first()
    chem = db.query(M.ChemistrySample).filter_by(heat_id=h.id, stage=stage,
                                                 superseded=False).all()
    mats = db.query(M.MaterialAddition).filter_by(heat_id=h.id, stage=stage,
                                                  superseded=False).all()
    dels = db.query(M.Delay).filter_by(heat_id=h.id, stage=stage,
                                       superseded=False).all()
    specs = L.grade_specs_map(db, h.grade_id)
    data = (rec.data or {}) if rec else {}
    entry = h.entry_stage or "eaf"
    # A backfilled historical record was never entered through this app, so it
    # is not measured against this app's entry rules — flagging a finished heat
    # from the archive as "5 blocking" is noise the shift can do nothing about.
    findings = [] if h.is_imported else validate_stage(
        stage, data,
        [{"values": c.values, "superseded": False} for c in chem],
        [{"quantity": m.quantity, "category": m.material.category,
          "point": m.addition_point, "superseded": False} for m in mats],
        [{"end_time": d.end_time, "reason_id": d.reason_id,
          "superseded": False} for d in dels], specs, entry)
    return {"heat": heat_summary(db, h),
            "stage": stage, "def": stage_def_for(entry, stage),
            "status": rec.status if rec else "pending",
            "version": rec.version if rec else 0,
            "data": data,
            "grade_specs": specs,
            "chemistry": [{"id": c.id, "label": c.sample_label,
                           "time": c.sample_time and c.sample_time.isoformat(),
                           "values": c.values} for c in chem],
            "materials": [{"id": m.id, "material_id": m.material_id,
                           "code": m.material.code, "name": m.material.name,
                           "qty": m.quantity, "unit": m.unit,
                           "time": m.addition_time and m.addition_time.isoformat(),
                           "point": m.addition_point or "process",
                           "category": m.material.category,
                           "remarks": m.remarks} for m in mats],
            "delays": [{"id": d.id, "start": d.start_time.isoformat(),
                        "end": d.end_time and d.end_time.isoformat(),
                        "min": d.duration_min, "reason_id": d.reason_id,
                        "reason": f"{d.reason.category} — {d.reason.reason}" if d.reason else "",
                        "remarks": d.remarks} for d in dels],
            "findings": findings}


class StageSave(BaseModel):
    data: dict
    version: int | None = None


@router.put("/heats/{heat_no}/stages/{stage}")
def save_stage(heat_no: str, stage: str, body: StageSave,
               user: M.User = Depends(require_perm("production.entry")),
               db: DBSession = Depends(get_db)):
    """Auto-save endpoint: saves draft data + recomputes auto-calculations.
    Optimistic locking via `version` protects concurrent pulpits."""
    h = _heat(db, heat_no)
    _guard_editable(h, user)
    _guard_stage_access(stage, user)
    rec = db.query(M.StageRecord).filter_by(heat_id=h.id, stage=stage).first()
    if not rec:
        raise HTTPException(404, "Stage not part of this heat's route")
    if body.version is not None and rec.version != body.version:
        # PRODUCTION SYNCHRONIZATION ENGINE: never "reload page", never silent
        # overwrite. Return everything the client needs to compare/merge.
        raise HTTPException(409, detail={
            "message": "PRODUCTION SYNCHRONIZATION ALERT — this heat has been "
                       "updated from another authorized workstation. Your current "
                       "changes have been safely preserved.",
            "server_version": rec.version,
            "server_data": rec.data or {},
            "updated_by": rec.updated_by_name or "another workstation",
        })
    mats = db.query(M.MaterialAddition).filter_by(heat_id=h.id, stage=stage,
                                                  superseded=False).all()
    merged = dict(rec.data or {}); merged.update(body.data or {})
    merged.update(auto_calculations(stage, merged,
                                    [{"quantity": m.quantity, "category": m.material.category,
                                      "point": m.addition_point} for m in mats]))
    rec.data = merged
    if rec.status == "pending":
        rec.status = "in_progress"
        rec.started_at = plant_time.now_moment().at
        L.emit(db, h, "stage_started", user,
               f"{STAGE_DEFS[stage]['title']} started.", stage=stage)
    rec.version += 1
    rec.updated_by_name = f"{user.name} ({user.role.name})"
    db.commit()
    return {"ok": True, "version": rec.version, "data": rec.data,
            "updated_by": rec.updated_by_name}


class ChemIn(BaseModel):
    label: str = ""
    sample_time: str | None = None
    values: dict


@router.post("/heats/{heat_no}/stages/{stage}/chemistry")
def add_chemistry(heat_no: str, stage: str, body: ChemIn,
                  user: M.User = Depends(require_perm("production.entry")),
                  db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    _guard_editable(h, user); _guard_stage_access(stage, user)
    st = None
    if body.sample_time:
        st = datetime.fromisoformat(body.sample_time)
    vals = {k: float(v) for k, v in body.values.items() if v not in (None, "")}
    db.add(M.ChemistrySample(heat_id=h.id, stage=stage, sample_label=body.label,
                             sample_time=st, values=vals, entered_by=user.id))
    L.emit(db, h, "note", user, f"Chemistry sample recorded ({body.label or stage}).",
           stage=stage, payload={"values": vals})
    db.commit()
    return {"ok": True}


@router.delete("/heats/{heat_no}/chemistry/{cid}")
def supersede_chem(heat_no: str, cid: int,
                   user: M.User = Depends(require_perm("production.entry")),
                   db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no); _guard_editable(h, user)
    c = db.get(M.ChemistrySample, cid)
    if not c or c.heat_id != h.id:
        raise HTTPException(404, "Sample not found")
    c.superseded = True   # corrections supersede — never delete (11.15)
    L.emit(db, h, "correction", user, "Chemistry sample superseded.", stage=c.stage)
    db.commit(); return {"ok": True}


class MatIn(BaseModel):
    material_id: int
    quantity: float
    unit: str | None = None
    addition_time: str | None = None
    # Where in the process this went in — the tile that recorded it says so.
    # "charge" and "tapping" additions live on the same stage as "process"
    # ones and would otherwise be indistinguishable.
    point: str = "process"             # charge|tapping|process
    remarks: str = ""


@router.post("/heats/{heat_no}/stages/{stage}/materials")
def add_material(heat_no: str, stage: str, body: MatIn,
                 user: M.User = Depends(require_perm("production.entry")),
                 db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    _guard_editable(h, user); _guard_stage_access(stage, user)
    mat = db.get(M.Material, body.material_id)
    if not mat:
        raise HTTPException(404, "Material not in Material Master")
    at = datetime.fromisoformat(body.addition_time) if body.addition_time else None
    point = body.point if body.point in ("charge", "tapping", "process") else "process"
    db.add(M.MaterialAddition(heat_id=h.id, stage=stage, material_id=mat.id,
                              quantity=body.quantity, unit=body.unit or mat.unit,
                              addition_time=at, addition_point=point,
                              remarks=body.remarks))
    # refresh autos (e.g. total charge weight)
    rec = db.query(M.StageRecord).filter_by(heat_id=h.id, stage=stage).first()
    if rec:
        mats = db.query(M.MaterialAddition).filter_by(heat_id=h.id, stage=stage,
                                                      superseded=False).all()
        merged = dict(rec.data or {})
        merged.update(auto_calculations(stage, merged,
                                        [{"quantity": x.quantity, "point": x.addition_point,
                                          "category": x.material.category} for x in mats]
                                        + [{"quantity": body.quantity, "point": point,
                                            "category": mat.category}]))
        rec.data = merged
    db.commit(); return {"ok": True}


@router.delete("/heats/{heat_no}/materials/{mid}")
def supersede_material(heat_no: str, mid: int,
                       user: M.User = Depends(require_perm("production.entry")),
                       db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no); _guard_editable(h, user)
    a = db.get(M.MaterialAddition, mid)
    if not a or a.heat_id != h.id:
        raise HTTPException(404, "Addition not found")
    a.superseded = True
    L.emit(db, h, "correction", user, f"Material addition superseded ({a.material.code}).", stage=a.stage)
    db.commit(); return {"ok": True}


class DelayIn(BaseModel):
    start_time: str
    end_time: str | None = None
    reason_id: int | None = None
    remarks: str = ""


@router.post("/heats/{heat_no}/stages/{stage}/delays")
def add_delay(heat_no: str, stage: str, body: DelayIn,
              user: M.User = Depends(require_perm("production.entry")),
              db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    _guard_editable(h, user); _guard_stage_access(stage, user)
    st = datetime.fromisoformat(body.start_time)
    en = datetime.fromisoformat(body.end_time) if body.end_time else None
    dur = None
    if en:
        try:
            dur = plant_time.duration_minutes(st, en)
        except ValueError:
            raise HTTPException(400, "Delay end time precedes start time")
    d = M.Delay(heat_id=h.id, stage=stage, start_time=st, end_time=en,
                duration_min=dur, reason_id=body.reason_id, remarks=body.remarks)
    db.add(d)
    if dur and dur >= 30:
        L.notify(db, f"Delay ≥30 min on Heat {h.heat_no}",
                 f"{STAGE_DEFS[stage]['title']}: {dur} min. {body.remarks}",
                 kind="delay", priority="high", heat_no=h.heat_no)
    L.emit(db, h, "note", user, f"Delay recorded ({dur or '—'} min).", stage=stage)
    db.commit(); return {"ok": True}


@router.delete("/heats/{heat_no}/delays/{did}")
def supersede_delay(heat_no: str, did: int,
                    user: M.User = Depends(require_perm("production.entry")),
                    db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no); _guard_editable(h, user)
    d = db.get(M.Delay, did)
    if not d or d.heat_id != h.id:
        raise HTTPException(404, "Delay not found")
    d.superseded = True
    db.commit(); return {"ok": True}


class RouteIn(BaseModel):
    route: str


class RefiningIn(BaseModel):
    route: str          # ZPF | LRF-5 | LRF-6


@router.post("/heats/{heat_no}/refining-route")
def refining_route(heat_no: str, body: RefiningIn,
                   user: M.User = Depends(get_current_user),
                   db: DBSession = Depends(get_db)):
    """THE PROCESS ROUTE LAW — 'Where shall this heat go next?' after EAF."""
    h = _heat(db, heat_no)
    _guard_editable(h, user)
    if user.role.name not in ("Shift Engineer", "Shift Incharge", "Administrator",
                              "Super Administrator", "Owner"):
        raise HTTPException(403, "The process route decision belongs to the Shift Engineer (5.8).")
    try:
        L.select_refining_route(db, h, user, body.route)
        db.commit()
    except ValueError as e:
        db.rollback(); raise HTTPException(400, str(e))
    return heat_payload(db, h)


@router.post("/heats/{heat_no}/route")
def route(heat_no: str, body: RouteIn,
          user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    _guard_editable(h, user)
    if user.role.name not in ("Shift Engineer", "Shift Incharge", "Administrator"):
        raise HTTPException(403, "The route decision belongs to the Shift Engineer (5.8).")
    try:
        L.select_route(db, h, user, body.route)
        db.commit()
    except ValueError as e:
        db.rollback(); raise HTTPException(400, str(e))
    return heat_payload(db, h)


@router.post("/heats/{heat_no}/stages/{stage}/submit")
def submit(heat_no: str, stage: str,
           user: M.User = Depends(require_perm("production.entry")),
           db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    _guard_editable(h, user); _guard_stage_access(stage, user)
    try:
        findings = L.submit_stage(db, h, stage, user)
        errors = [f for f in findings if f["level"] == "error"]
        if errors:
            db.commit()  # the validation event is knowledge — keep it
            return {"ok": False, "findings": findings}
        db.commit()
        return {"ok": True, "findings": findings, "heat": heat_payload(db, h)}
    except ValueError as e:
        db.rollback(); raise HTTPException(400, str(e))


@router.get("/heats/{heat_no}/review")
def review(heat_no: str, user: M.User = Depends(get_current_user),
           db: DBSession = Depends(get_db)):
    from ..engines.reports import heat_report_data
    h = _heat(db, heat_no)
    return {"heat": heat_payload(db, h),
            # Imported history carries no findings — see get_stage.
            "validation": [] if h.is_imported else L.final_validation(db, h),
            "report": heat_report_data(db, h)}


class ApproveIn(BaseModel):
    level: int
    remarks: str = ""


@router.post("/heats/{heat_no}/approve")
def approve(heat_no: str, body: ApproveIn,
            user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    try:
        L.approve(db, h, user, body.level, body.remarks)
        db.commit()
    except ValueError as e:
        db.rollback(); raise HTTPException(400, str(e))
    return heat_payload(db, h)


class UnlockIn(BaseModel):
    reason: str


@router.post("/heats/{heat_no}/unlock")
def unlock(heat_no: str, body: UnlockIn,
           user: M.User = Depends(require_perm("admin")), db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    try:
        L.unlock_heat(db, h, user, body.reason)
        db.commit()
    except ValueError as e:
        db.rollback(); raise HTTPException(400, str(e))
    return heat_payload(db, h)


@router.get("/heats/{heat_no}/timeline")
def timeline(heat_no: str, user: M.User = Depends(get_current_user),
             db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    evs = db.query(M.HeatEvent).filter_by(heat_id=h.id).order_by(M.HeatEvent.at).all()
    return {"heat": heat_summary(db, h),
            "events": [{"at": e.at.isoformat(sep=" "), "plant_day": e.plant_day.isoformat(),
                        "shift": e.shift, "kind": e.kind, "stage": e.stage,
                        "actor": e.actor_name, "detail": e.detail}
                       for e in evs]}


# ─────────── Metallurgical Calculation Engine API ───────────

class CalcIn(BaseModel):
    heat_weight_t: float | None = None
    current: dict = {}
    target: dict = {}
    heat_no: str | None = None   # auto-populate from active heat (6D.8)


@router.post("/calculator/alloys")
def calc_alloys(body: CalcIn, user: M.User = Depends(get_current_user),
                db: DBSession = Depends(get_db)):
    current, target, hw = dict(body.current), dict(body.target), body.heat_weight_t
    if body.heat_no:
        h = _heat(db, body.heat_no)
        if not hw:
            eaf = db.query(M.StageRecord).filter_by(heat_id=h.id, stage="eaf").first()
            hw = (eaf.data or {}).get("tap_weight") if eaf else None
        if not current:
            c = (db.query(M.ChemistrySample)
                 .filter_by(heat_id=h.id, superseded=False)
                 .order_by(M.ChemistrySample.entered_at.desc()).first())
            if c:
                current = c.values   # 6D.16: always the latest available chemistry
        if not target:
            target = {el: s["aim"] for el, s in L.grade_specs_map(db, h.grade_id).items()}
    alloys = [{"code": a.code, "name": a.name, "composition": a.composition}
              for a in db.query(M.Material).filter_by(category="alloy", archived=False)]
    res = alloy_requirements(hw, current, target, alloys)
    res["inputs"] = {"heat_weight_t": hw, "current": current, "target": target}
    return res


class FluxIn(BaseModel):
    heat_weight_t: float | None = None
    sulphur_pct: float | None = None


@router.post("/calculator/flux")
def calc_flux(body: FluxIn, user: M.User = Depends(get_current_user)):
    return flux_recommendation(body.heat_weight_t, body.sulphur_pct)


class WireIn(BaseModel):
    heat_weight_t: float | None = None
    target_ca_ppm: float = 25
    wire_gpm: float = 220
    recovery_pct: float = 12


@router.post("/calculator/wire")
def calc_wire(body: WireIn, user: M.User = Depends(get_current_user)):
    return wire_recommendation(body.heat_weight_t, body.target_ca_ppm,
                               body.wire_gpm, body.recovery_pct)


# ═══════════ CORRECTION SYSTEM & VERSION CONTROL (Constitution S.3) ═══════════
# OLD VALUE → PRESERVED → CORRECTION REQUEST → APPROVAL → NEW VALUE →
# VERSION → AUDIT LOG. The ONLY modification path for locked records.

CORRECTION_APPROVERS = ("Shift Incharge", "Administrator", "Super Administrator", "Owner")


class CorrectionIn(BaseModel):
    stage: str
    changes: dict          # {field: new_value}
    reason: str


@router.post("/heats/{heat_no}/corrections")
def request_correction(heat_no: str, body: CorrectionIn,
                       user: M.User = Depends(require_perm("production.entry")),
                       db: DBSession = Depends(get_db)):
    h = _heat(db, heat_no)
    if h.aborted:
        raise HTTPException(400, "Aborted heats preserve their final state; corrections are not applicable.")
    if body.stage not in STAGE_DEFS:
        raise HTTPException(404, "Unknown stage")
    if not body.reason.strip():
        raise HTTPException(400, "A correction reason is mandatory (Constitution S.3).")
    if not body.changes:
        raise HTTPException(400, "No field changes supplied.")
    rec = db.query(M.StageRecord).filter_by(heat_id=h.id, stage=body.stage).first()
    if not rec:
        raise HTTPException(404, "Stage not part of this heat's route")
    old = rec.data or {}
    diff = {k: {"old": old.get(k), "new": v} for k, v in body.changes.items()
            if old.get(k) != v}
    if not diff:
        raise HTTPException(400, "The proposed values are identical to the current values.")
    cr = M.CorrectionRequest(heat_id=h.id, heat_no=h.heat_no, stage=body.stage,
                             changes=diff, reason=body.reason,
                             requested_by=user.id, requested_by_name=user.name)
    db.add(cr)
    L.emit(db, h, "correction", user,
           f"Correction requested on {STAGE_DEFS[body.stage]['title']}: "
           f"{', '.join(diff.keys())} — {body.reason}", stage=body.stage,
           payload={"changes": diff})
    L.audit(db, user, "production", "correction_requested", h.heat_no,
            f"{body.stage}: {', '.join(diff.keys())}")
    L.notify(db, f"Correction pending approval — Heat {h.heat_no}",
             f"{user.name}: {body.reason}", kind="approval", priority="high",
             heat_no=h.heat_no, audience_role="Shift Incharge")
    db.commit()
    return {"ok": True, "id": cr.id, "changes": diff}


@router.get("/corrections")
def list_corrections(status: str = "pending", heat_no: str | None = None,
                     user: M.User = Depends(get_current_user),
                     db: DBSession = Depends(get_db)):
    q = db.query(M.CorrectionRequest)
    if status:
        q = q.filter(M.CorrectionRequest.status == status)
    if heat_no:
        q = q.filter(M.CorrectionRequest.heat_no == heat_no.upper())
    return [{"id": c.id, "heat_no": c.heat_no, "stage": c.stage,
             "stage_title": STAGE_DEFS[c.stage]["title"],
             "changes": c.changes, "reason": c.reason, "status": c.status,
             "requested_by": c.requested_by_name,
             "requested_at": c.requested_at.isoformat(sep=" "),
             "decided_by": c.decided_by_name,
             "decided_at": c.decided_at and c.decided_at.isoformat(sep=" "),
             "decision_remarks": c.decision_remarks,
             "applied_version": c.applied_version,
             "can_decide": user.role.name in CORRECTION_APPROVERS}
            for c in q.order_by(M.CorrectionRequest.requested_at.desc()).limit(200)]


class DecideIn(BaseModel):
    approve: bool
    remarks: str = ""


@router.post("/corrections/{cid}/decide")
def decide_correction(cid: int, body: DecideIn,
                      user: M.User = Depends(get_current_user),
                      db: DBSession = Depends(get_db)):
    if user.role.name not in CORRECTION_APPROVERS:
        raise HTTPException(403, f"Correction approval requires: {', '.join(CORRECTION_APPROVERS)}.")
    cr = db.get(M.CorrectionRequest, cid)
    if not cr:
        raise HTTPException(404, "Correction request not found")
    if cr.status != "pending":
        raise HTTPException(400, f"Request already {cr.status}.")
    if cr.requested_by == user.id and user.role.name not in ("Administrator", "Super Administrator", "Owner"):
        raise HTTPException(403, "A correction cannot be approved by its own requester.")
    h = db.get(M.Heat, cr.heat_id)
    m = plant_time.now_moment()
    cr.decided_by = user.id
    cr.decided_by_name = user.name
    cr.decided_at = m.at
    cr.decision_remarks = body.remarks
    if not body.approve:
        cr.status = "rejected"
        L.emit(db, h, "correction", user,
               f"Correction REJECTED on {STAGE_DEFS[cr.stage]['title']}: {body.remarks or 'no remarks'}",
               stage=cr.stage)
        L.audit(db, user, "approval", "correction_rejected", cr.heat_no, cr.stage)
        db.commit()
        return {"ok": True, "status": "rejected"}
    rec = db.query(M.StageRecord).filter_by(heat_id=cr.heat_id, stage=cr.stage).first()
    merged = dict(rec.data or {})
    for k, ch in (cr.changes or {}).items():
        merged[k] = ch["new"]
    mats = db.query(M.MaterialAddition).filter_by(heat_id=cr.heat_id, stage=cr.stage,
                                                  superseded=False).all()
    merged.update(auto_calculations(cr.stage, merged,
                                    [{"quantity": x.quantity, "point": x.addition_point,
                                      "category": x.material.category} for x in mats]))
    rec.data = merged
    rec.version += 1
    cr.status = "approved"
    cr.applied_version = rec.version
    db.add(M.RecordVersion(heat_id=cr.heat_id, heat_no=cr.heat_no, scope=cr.stage,
                           version_no=rec.version, kind="correction",
                           data=dict(merged), changes=cr.changes,
                           reason=cr.reason, created_by=user.id,
                           created_by_name=user.name))
    L.emit(db, h, "correction", user,
           f"Correction APPROVED & applied on {STAGE_DEFS[cr.stage]['title']} "
           f"(→ version {rec.version}): {', '.join((cr.changes or {}).keys())}",
           stage=cr.stage, payload={"changes": cr.changes, "version": rec.version})
    L.audit(db, user, "approval", "correction_approved", cr.heat_no,
            f"{cr.stage} v{rec.version}")
    L.notify(db, f"Correction applied — Heat {cr.heat_no}",
             f"Approved by {user.name}. {STAGE_DEFS[cr.stage]['title']} is now version {rec.version}.",
             kind="production", heat_no=cr.heat_no)
    db.commit()
    return {"ok": True, "status": "approved", "version": rec.version}


@router.get("/heats/{heat_no}/versions")
def heat_versions(heat_no: str, stage: str | None = None,
                  user: M.User = Depends(get_current_user),
                  db: DBSession = Depends(get_db)):
    """Digital Time Machine: complete version history of a heat's records."""
    h = _heat(db, heat_no)
    q = db.query(M.RecordVersion).filter_by(heat_id=h.id)
    if stage:
        q = q.filter(M.RecordVersion.scope == stage)
    return [{"id": v.id, "stage": v.scope, "stage_title": STAGE_DEFS[v.scope]["title"],
             "version_no": v.version_no, "kind": v.kind,
             "at": v.created_at.isoformat(sep=" "), "by": v.created_by_name,
             "reason": v.reason, "changes": v.changes, "data": v.data}
            for v in q.order_by(M.RecordVersion.created_at)]


@router.get("/grades/search")
def grade_search(q: str = "", user: M.User = Depends(get_current_user),
                 db: DBSession = Depends(get_db)):
    """GRADE ENTRY SYSTEM: search & reuse historical grades. Every grade ever
    used lives in the master forever — nothing is ever lost."""
    query = db.query(M.SteelGrade)
    if q.strip():
        query = query.filter(M.SteelGrade.code.ilike(f"%{q.strip()}%"))
    out = []
    for g in query.order_by(M.SteelGrade.code).limit(50):
        used = db.query(M.Heat).filter_by(grade_id=g.id).count()
        out.append({"id": g.id, "code": g.code, "heats": used,
                    "has_specs": len(g.specs) > 0})
    return out
