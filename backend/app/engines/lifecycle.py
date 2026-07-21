"""
SMS-2 IDOS — HEAT LIFECYCLE ENGINE (Section 5)
===============================================
The frozen production state machine:

  created → eaf (charge mix + furnace) → zpf → lrf → [route decision]
          → (vd) → caster → quality → review → LOCKED

Gating is computed, never hardcoded navigation (5.8: only required modules
become available). Every transition writes a HeatEvent (11.9). Locked heats
are read-only; unlock requires Administrator + audit (6H.11). Aborted /
diverted heats are first-class audited exception paths — paper tolerates
exceptions, so must SMS-2 (Preserve Existing Workflow, 3.4).
"""
from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from .. import models as M
from ..core import plant_time
from .stage_config import STAGE_DEFS, required_stages, REFINING_ROUTES
from .validation import validate_stage

APPROVAL_CHAIN = [   # configurable via Administration (10.6); default per 5.13
    {"level": 1, "role": "Operator"},
    {"level": 2, "role": "Shift Engineer"},
    {"level": 3, "role": "Shift Incharge"},
]


def emit(db: Session, heat: M.Heat | None, kind: str, actor: M.User | None,
         detail: str = "", stage: str | None = None, payload: dict | None = None):
    m = plant_time.now_moment()
    db.add(M.HeatEvent(
        heat_id=heat.id if heat else None,
        heat_no=heat.heat_no if heat else None,
        at=m.at, plant_day=m.plant_day, shift=m.shift,
        kind=kind, stage=stage,
        actor_id=actor.id if actor else None,
        actor_name=actor.name if actor else "System",
        detail=detail, payload=payload or {}))


def audit(db: Session, user: M.User | None, category: str, action: str,
          heat_no: str | None = None, detail: str = ""):
    db.add(M.AuditLog(user_id=user.id if user else None,
                      user_name=user.name if user else "System",
                      category=category, action=action,
                      heat_no=heat_no, detail=detail))


def notify(db: Session, title: str, body: str = "", kind: str = "production",
           priority: str = "normal", heat_no: str | None = None,
           audience_role: str | None = None):
    db.add(M.Notification(title=title, body=body, kind=kind,
                          priority=priority, heat_no=heat_no,
                          audience_role=audience_role))
    if priority == "critical":
        from .notifier import email_critical
        email_critical(db, title, body)


def next_heat_no(db: Session, unit: str = "eaf") -> str:
    """Next number from the series belonging to `unit`'s furnace.
    The plant numbers EAF heats E… and ZPF heats F…, so the series must be
    chosen by furnace — taking the first active series would always hand back
    an E number and silently mis-number every ZPF heat."""
    s = db.query(M.HeatSeries).filter_by(active=True, unit=unit).first()
    if not s:
        # Legacy databases carry a single series with no unit set.
        s = db.query(M.HeatSeries).filter_by(active=True).first()
    if not s:
        s = M.HeatSeries(prefix="E", next_number=260001, pad=6, unit="eaf")
        db.add(s); db.flush()
    no = f"{s.prefix}{str(s.next_number).zfill(s.pad)}"
    s.next_number += 1
    return no


def create_heat(db: Session, user: M.User, grade_id: int, heat_no: str | None,
                shift_engineer: str, is_training: bool = False,
                entry_stage: str = "eaf") -> M.Heat:
    entry_stage = entry_stage if entry_stage in ("eaf", "zpf") else "eaf"
    if heat_no:
        heat_no = heat_no.strip().upper()
        if db.query(M.Heat).filter_by(heat_no=heat_no).first():
            raise ValueError(f"Duplicate Heat Number {heat_no} — heat numbers are unique and never reused (5.3).")
    else:
        heat_no = next_heat_no(db, entry_stage)
    m = plant_time.now_moment()
    heat = M.Heat(heat_no=heat_no, grade_id=grade_id, status=entry_stage,
                  entry_stage=entry_stage,
                  plant_day=m.plant_day, shift=m.shift,
                  shift_engineer=shift_engineer, created_by=user.id,
                  is_training=is_training)
    db.add(heat); db.flush()
    # Pre-create the always-required stage rows (VD added on route selection).
    # The furnace this heat did NOT start in gets no record — a ZPF heat never
    # sees the EAF, and vice versa.
    stages = ["eaf", "zpf", "lrf", "caster", "quality", "review"]
    if entry_stage == "zpf":
        stages.remove("eaf")
    for st in stages:
        db.add(M.StageRecord(heat_id=heat.id, stage=st,
                             plant_day=m.plant_day, shift=m.shift))
    emit(db, heat, "created", user,
         f"Heat {heat_no} created ({'TRAINING' if is_training else 'production'}) "
         f"in the {'ZPF' if entry_stage == 'zpf' else 'EAF'}. "
         f"Plant Day {m.plant_day}, Shift {m.shift}.")
    audit(db, user, "production", "heat_created", heat_no)
    return heat


STAGE_SEQUENCE = ["eaf", "zpf", "lrf", "vd", "caster", "quality", "review"]


def stage_gates(db: Session, heat: M.Heat) -> dict:
    """Compute unlock state of every stage for a heat (data-driven gating)."""
    recs = {r.stage: r for r in db.query(M.StageRecord).filter_by(heat_id=heat.id)}
    zpf_rec = recs.get("zpf")
    has_zpf_data = bool(zpf_rec and (zpf_rec.data or zpf_rec.status in ("submitted", "approved")))
    entry = heat.entry_stage or "eaf"
    req = required_stages(heat.route, heat.route_refining, has_zpf_data, entry)
    gates = {}
    prev_done = True
    for st in STAGE_SEQUENCE:
        # The furnace this heat did not start in is simply not on its route.
        if st == "eaf" and entry == "zpf":
            gates[st] = {"state": "not_in_route",
                         "reason": "This heat was melted in the ZPF — the Electric Arc Furnace is not part of it."}
            continue
        if st == "zpf" and st not in req:
            if heat.route_refining is None:
                gates[st] = {"state": "awaiting_route",
                             "reason": "Available only if selected in the post-EAF Process Route decision (ZPF / LRF-5 / LRF-6)."}
            else:
                gates[st] = {"state": "not_in_route",
                             "reason": f"Route {heat.route_refining} takes this heat directly to the Ladle Refining Furnace — ZPF is not part of this heat."}
            continue
        if st == "vd" and heat.route not in ("VD-2", "VD-3"):
            if heat.route is None:
                gates[st] = {"state": "awaiting_route", "reason": "Available only if selected in the LRF Route Decision (6E.14)."}
            else:
                gates[st] = {"state": "not_in_route", "reason": f"Route {heat.route} bypasses Vacuum Degassing."}
            continue
        rec = recs.get(st)
        submitted = rec is not None and rec.status in ("submitted", "approved")
        if heat.aborted:
            gates[st] = {"state": "aborted", "reason": heat.abort_reason}
        elif st in req and prev_done:
            # The route decision hangs off the ENTRY furnace, not off the EAF:
            # a ZPF heat has no EAF record, so keying on the EAF would leave
            # its LRF gate open and skip the decision entirely.
            entry_rec = recs.get(entry)
            route_pending = (heat.route_refining is None and entry_rec is not None
                             and entry_rec.status in ("submitted", "approved"))
            _opts = "ZPF / LRF-5 / LRF-6" if entry == "eaf" else "LRF-5 / LRF-6"
            if route_pending and (st == "lrf" or (st == "zpf" and entry == "eaf")):
                gates[st] = {"state": "locked",
                             "reason": f"Where shall this heat go next? Select the process route "
                                       f"after {entry.upper()} ({_opts})."}
            elif st == "caster" and heat.route is None:
                gates[st] = {"state": "locked", "reason": "Route decision pending at LRF (6D.16)."}
            else:
                gates[st] = {"state": "done" if submitted else "open", "reason": ""}
        elif st in req:
            gates[st] = {"state": "locked", "reason": "Previous stage not yet submitted (5.2 gated workflow)."}
        if st in req:
            prev_done = prev_done and submitted
    return gates


def select_refining_route(db: Session, heat: M.Heat, user: M.User, choice: str):
    """THE PROCESS ROUTE LAW — post-EAF decision: where shall this heat go next?
    ZPF · LRF-5 · LRF-6. Selecting an LRF unit bypasses ZPF entirely; ZPF is
    never mandatory and is never displayed as required for such heats."""
    entry = heat.entry_stage or "eaf"
    if choice not in REFINING_ROUTES:
        raise ValueError(f"Process route must be one of {', '.join(REFINING_ROUTES)}.")
    # A ZPF-melted heat has already been through the ZPF; its only remaining
    # refining choice is which Ladle Refining Furnace takes it.
    if entry == "zpf" and choice == "ZPF":
        raise ValueError("This heat was melted in the ZPF — it has already been through it. "
                         "Select the Ladle Refining Furnace: LRF-5 or LRF-6.")
    entry_rec = db.query(M.StageRecord).filter_by(heat_id=heat.id, stage=entry).first()
    if not entry_rec or entry_rec.status not in ("submitted", "approved"):
        raise ValueError(f"The post-{entry.upper()} route decision becomes available "
                         f"once the {entry.upper()} module is submitted.")
    zpf = db.query(M.StageRecord).filter_by(heat_id=heat.id, stage="zpf").first()
    if choice != "ZPF" and zpf and (zpf.data or zpf.status in ("submitted", "approved")):
        raise ValueError("This heat already carries ZPF production data — it cannot be re-routed to bypass ZPF. "
                         "Use the Correction System if the ZPF record was made in error.")
    old = heat.route_refining
    heat.route_refining = choice
    m = plant_time.now_moment()
    if choice == "ZPF":
        if not zpf:
            db.add(M.StageRecord(heat_id=heat.id, stage="zpf",
                                 plant_day=m.plant_day, shift=m.shift))
    else:
        # LRF unit pre-selected from the route decision (still editable in LRF)
        lrf = db.query(M.StageRecord).filter_by(heat_id=heat.id, stage="lrf").first()
        if not lrf:
            lrf = M.StageRecord(heat_id=heat.id, stage="lrf",
                                plant_day=m.plant_day, shift=m.shift, data={})
            db.add(lrf); db.flush()
        d = dict(lrf.data or {})
        d.setdefault("lrf_unit", choice)
        lrf.data = d
    verb = "re-selected" if old else "selected"
    emit(db, heat, "route_selected", user,
         f"Post-{entry.upper()} process route {verb}: {choice}" + (f" (was {old})" if old else "")
         + ("" if choice == "ZPF" or entry == "zpf" else " — this heat bypasses ZPF"),
         stage=entry, payload={"refining_route": choice, "previous": old})
    audit(db, user, "production", "refining_route_selected", heat.heat_no, choice)
    notify(db, f"Process route {choice} selected for Heat {heat.heat_no}",
           f"Selected by {user.name}." + ("" if choice == "ZPF" else " This heat bypasses ZPF."),
           heat_no=heat.heat_no)
    heat.status = _advance_status(db, heat)


def select_route(db: Session, heat: M.Heat, user: M.User, route: str):
    if route not in ("VD-2", "VD-3", "BC-2", "BC-3"):
        raise ValueError("Route must be one of VD-2, VD-3, BC-2, BC-3 (5.8).")
    lrf = db.query(M.StageRecord).filter_by(heat_id=heat.id, stage="lrf").first()
    chem = db.query(M.ChemistrySample).filter_by(heat_id=heat.id, stage="lrf",
                                                 superseded=False).count()
    if chem == 0:
        raise ValueError("Chemistry shall be validated before route selection (6D.16) — record LRF chemistry first.")
    old = heat.route
    heat.route = route
    if route.startswith("VD"):
        heat.route_vd = route
        heat.route_caster = None  # caster chosen at VD completion? No — doc: VD then selected caster. Keep caster selectable in caster module.
        if not db.query(M.StageRecord).filter_by(heat_id=heat.id, stage="vd").first():
            m = plant_time.now_moment()
            db.add(M.StageRecord(heat_id=heat.id, stage="vd",
                                 plant_day=m.plant_day, shift=m.shift))
    else:
        heat.route_vd = None
        heat.route_caster = route
    verb = "re-selected" if old else "selected"
    emit(db, heat, "route_selected", user,
         f"Process route {verb}: {route}" + (f" (was {old})" if old else ""),
         stage="lrf", payload={"route": route, "previous": old})
    audit(db, user, "production", "route_selected", heat.heat_no, route)
    notify(db, f"Route {route} selected for Heat {heat.heat_no}",
           f"Selected by {user.name}.", heat_no=heat.heat_no)


def submit_stage(db: Session, heat: M.Heat, stage: str, user: M.User) -> list[dict]:
    """Validate + submit a stage. Returns blocking findings if any (empty = success)."""
    if heat.status == "locked":
        raise ValueError("Heat is locked and read-only (6H.11).")
    gates = stage_gates(db, heat)
    g = gates.get(stage, {})
    if g.get("state") not in ("open", "done"):
        raise ValueError(f"Stage is not available: {g.get('reason', 'locked')}")
    rec = db.query(M.StageRecord).filter_by(heat_id=heat.id, stage=stage).first()
    chem = [ {"values": c.values, "superseded": c.superseded}
             for c in db.query(M.ChemistrySample).filter_by(heat_id=heat.id, stage=stage)]
    mats = [ {"quantity": a.quantity, "category": a.material.category,
              "point": a.addition_point, "superseded": a.superseded}
             for a in db.query(M.MaterialAddition).filter_by(heat_id=heat.id, stage=stage)]
    dels = [ {"end_time": d.end_time, "reason_id": d.reason_id, "superseded": d.superseded}
             for d in db.query(M.Delay).filter_by(heat_id=heat.id, stage=stage)]
    specs = grade_specs_map(db, heat.grade_id)
    findings = validate_stage(stage, rec.data or {}, chem, mats, dels, specs,
                              heat.entry_stage or "eaf")
    errors = [f for f in findings if f["level"] == "error"]
    if stage == "lrf" and heat.route is None:
        errors.append({"level": "error", "tile": "Process Route Decision",
                       "message": "Route selection is mandatory before LRF submission (6D.16)."})
    emit(db, heat, "validation", user,
         f"{STAGE_DEFS[stage]['title']}: {len(errors)} error(s), "
         f"{len([f for f in findings if f['level']=='warning'])} warning(s).",
         stage=stage, payload={"findings": findings})
    if errors:
        return errors + [f for f in findings if f["level"] != "error"]
    m = plant_time.now_moment()
    rec.status = "submitted"
    rec.submitted_at = m.at
    rec.submitted_by = user.id
    # VERSION CONTROL (Constitution S.3): immutable snapshot on submission
    db.add(M.RecordVersion(heat_id=heat.id, heat_no=heat.heat_no, scope=stage,
                           version_no=rec.version, kind="submit",
                           data=dict(rec.data or {}),
                           created_by=user.id, created_by_name=user.name,
                           reason="Stage submitted"))
    heat.status = _advance_status(db, heat)
    emit(db, heat, "stage_submitted", user,
         f"{STAGE_DEFS[stage]['title']} submitted.", stage=stage)
    audit(db, user, "production", "stage_submitted", heat.heat_no, stage)
    nxt = _next_open_stage(db, heat)
    if nxt:
        notify(db, f"Heat {heat.heat_no}: {STAGE_DEFS[nxt]['title']} unlocked",
               f"{STAGE_DEFS[stage]['title']} completed by {user.name}.",
               heat_no=heat.heat_no)
    return [f for f in findings if f["level"] != "error"]  # warnings ride along


def heat_required_stages(db: Session, heat: M.Heat) -> list[str]:
    """Stages actually followed by THIS heat (Process Route Law)."""
    z = db.query(M.StageRecord).filter_by(heat_id=heat.id, stage="zpf").first()
    return required_stages(heat.route, heat.route_refining,
                           bool(z and (z.data or z.status in ("submitted", "approved"))),
                           heat.entry_stage or "eaf")


def _advance_status(db: Session, heat: M.Heat) -> str:
    gates = stage_gates(db, heat)
    for st in heat_required_stages(db, heat):
        if gates.get(st, {}).get("state") == "open":
            return st
    return "review"


def _next_open_stage(db: Session, heat: M.Heat):
    gates = stage_gates(db, heat)
    for st in heat_required_stages(db, heat):
        if gates.get(st, {}).get("state") == "open":
            return st
    return None


def grade_specs_map(db: Session, grade_id: int | None) -> dict:
    if not grade_id:
        return {}
    specs = db.query(M.GradeSpec).filter_by(grade_id=grade_id).all()
    return {s.element: {"min": s.min_pct, "aim": s.aim_pct, "max": s.max_pct}
            for s in specs}


def final_validation(db: Session, heat: M.Heat) -> list[dict]:
    """Whole-heat validation for Review & Submit (6H.8)."""
    findings = []
    req = heat_required_stages(db, heat)
    recs = {r.stage: r for r in db.query(M.StageRecord).filter_by(heat_id=heat.id)}
    for st in req:
        if st == "review":
            continue
        r = recs.get(st)
        if not r or r.status not in ("submitted", "approved"):
            findings.append({"level": "error", "tile": STAGE_DEFS[st]["title"],
                             "message": f"{STAGE_DEFS[st]['title']} is not submitted."})
    if heat.route is None:
        findings.append({"level": "error", "tile": "Route",
                         "message": "Missing route selection (5.12)."})
    q = recs.get("quality")
    qdec = (q.data or {}).get("decision") if q else None
    if qdec not in ("Accepted", "Accepted with Remarks"):
        findings.append({"level": "error", "tile": "Quality",
                         "message": f"Quality approval is mandatory before final submission (6G.13). Current decision: {qdec or 'none'}."})
    return findings


def approve(db: Session, heat: M.Heat, user: M.User, level: int, remarks: str = ""):
    chain = APPROVAL_CHAIN
    step = next((c for c in chain if c["level"] == level), None)
    if not step:
        raise ValueError("Unknown approval level.")
    role = user.role.name
    if role != step["role"] and role != "Administrator":
        raise ValueError(f"Level {level} approval requires role {step['role']} (your role: {role}).")
    existing = {a.level for a in db.query(M.Approval)
                .filter_by(heat_id=heat.id, scope="final", decision="approved")}
    if level in existing:
        raise ValueError(f"Level {level} is already approved.")
    if any(l not in existing for l in range(1, level)):
        raise ValueError("Approvals must follow the chain: Operator → Shift Engineer → Shift Incharge (5.13).")
    errs = final_validation(db, heat)
    if [e for e in errs if e["level"] == "error"]:
        raise ValueError("Validation errors prevent approval — resolve them in Review (6H.8).")
    m = plant_time.now_moment()
    sig = f"SIG:{user.employee_id}:{m.at.isoformat()}"
    db.add(M.Approval(heat_id=heat.id, scope="final", level=level,
                      role_name=step["role"], user_id=user.id, user_name=user.name,
                      signature=sig, remarks=remarks, at=m.at))
    emit(db, heat, "approval", user,
         f"Level {level} ({step['role']}) approval recorded.", payload={"signature": sig})
    audit(db, user, "approval", f"final_approval_L{level}", heat.heat_no)
    if level == max(c["level"] for c in chain):
        lock_heat(db, heat, user)
    else:
        nxt = next(c for c in chain if c["level"] == level + 1)
        notify(db, f"Heat {heat.heat_no} awaiting {nxt['role']} approval",
               f"Level {level} approved by {user.name}.", kind="approval",
               priority="high", heat_no=heat.heat_no, audience_role=nxt["role"])


def lock_heat(db: Session, heat: M.Heat, user: M.User):
    m = plant_time.now_moment()
    heat.status = "locked"
    heat.locked_at = m.at
    rev = db.query(M.StageRecord).filter_by(heat_id=heat.id, stage="review").first()
    if rev:
        rev.status = "submitted"; rev.submitted_at = m.at; rev.submitted_by = user.id
    emit(db, heat, "locked", user,
         "Production record locked. Heat is now read-only and archived (6H.11).")
    audit(db, user, "production", "heat_locked", heat.heat_no)
    notify(db, f"Heat {heat.heat_no} completed and locked",
           f"Final approval by {user.name}. Record archived.",
           kind="production", priority="high", heat_no=heat.heat_no)


def unlock_heat(db: Session, heat: M.Heat, admin: M.User, reason: str):
    if admin.role.name != "Administrator":
        raise ValueError("Unlocking requires Administrator authorization (6H.11).")
    if not reason.strip():
        raise ValueError("Unlock reason is mandatory — every unlock is audited.")
    heat.status = "review"
    heat.locked_at = None
    # approvals are superseded, never deleted: mark rejected-history via event
    for a in db.query(M.Approval).filter_by(heat_id=heat.id, scope="final"):
        a.decision = "superseded_by_unlock"
    emit(db, heat, "unlocked", admin, f"ADMIN UNLOCK: {reason}")
    audit(db, admin, "admin", "heat_unlocked", heat.heat_no, reason)
    notify(db, f"Heat {heat.heat_no} unlocked by Administrator",
           reason, kind="admin", priority="critical", heat_no=heat.heat_no)


def abort_heat(db: Session, heat: M.Heat, user: M.User, reason: str):
    if heat.status == "locked":
        raise ValueError("Locked heats cannot be aborted.")
    if not reason.strip():
        raise ValueError("Abort/divert reason is mandatory (exception paths are audited knowledge).")
    heat.aborted = True
    heat.abort_reason = reason
    heat.status = "aborted"
    emit(db, heat, "aborted", user, f"Heat aborted/diverted: {reason}")
    audit(db, user, "production", "heat_aborted", heat.heat_no, reason)
    notify(db, f"Heat {heat.heat_no} aborted", reason, priority="critical",
           heat_no=heat.heat_no)
