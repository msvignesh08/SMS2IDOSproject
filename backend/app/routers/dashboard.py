"""Digital Command Center API (Section 8): one payload answers the seven
five-second questions. Pre-aggregated server-side so the dashboard renders
fast on plant hardware."""
from __future__ import annotations
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession
from ..core.database import get_db
from ..core import plant_time
from .. import models as M
from ..engines import lifecycle as L
from ..engines.stage_config import STAGE_DEFS
from .auth import get_current_user

router = APIRouter(prefix="/api", tags=["dashboard"])

STAGE_TITLES = {k: v["title"] for k, v in STAGE_DEFS.items()}


@router.get("/dashboard")
def dashboard(user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    m = plant_time.now_moment()
    day_start, day_end = plant_time.plant_day_window(m.plant_day)

    heats_today = (db.query(M.Heat)
                   .filter(M.Heat.plant_day == m.plant_day,
                           M.Heat.is_training == False,          # noqa: E712
                           M.Heat.archived == False).all())      # noqa: E712
    active = [h for h in heats_today if h.status not in ("locked", "aborted")]
    # include active heats from previous plant days (long-running / pending)
    carry = (db.query(M.Heat)
             .filter(M.Heat.plant_day < m.plant_day,
                     M.Heat.status.notin_(["locked", "aborted"]),
                     M.Heat.is_training == False,                # noqa: E712
                     M.Heat.archived == False).all())            # noqa: E712
    active_all = active + carry

    completed = [h for h in heats_today if h.status == "locked"]

    # production totals for the plant day
    tot_billet_t = tot_billets = tot_energy = tot_o2 = 0.0
    for h in heats_today:
        for r in db.query(M.StageRecord).filter_by(heat_id=h.id):
            d = r.data or {}
            if r.stage == "caster":
                tot_billet_t += float(d.get("billet_weight") or 0)
                tot_billets += int(float(d.get("billet_count") or 0))
            if r.stage == "eaf":
                tot_energy += float(d.get("energy_kwh") or 0)
                tot_o2 += float(d.get("o2_consumption") or 0)

    # pending approvals for my role
    pending_approvals = []
    for h in db.query(M.Heat).filter(M.Heat.status.in_(["review"]),
                                     M.Heat.is_training == False,   # noqa
                                     M.Heat.archived == False):     # noqa
        levels = {a.level for a in db.query(M.Approval)
                  .filter_by(heat_id=h.id, scope="final", decision="approved")}
        nxt = next((c for c in L.APPROVAL_CHAIN if c["level"] not in levels), None)
        if nxt:
            pending_approvals.append({"heat_no": h.heat_no,
                                      "awaiting": nxt["role"], "level": nxt["level"],
                                      "mine": nxt["role"] == user.role.name
                                              or user.role.name == "Administrator"})

    # open delays (no end time) — production issues NOW
    open_delays = (db.query(M.Delay).join(M.Heat, M.Delay.heat_id == M.Heat.id)
                   .filter(M.Delay.end_time.is_(None), M.Delay.superseded == False,  # noqa
                           M.Heat.status.notin_(["locked", "aborted"])).all())

    # gantt: stage windows per active heat from event stream
    gantt = []
    for h in active_all:
        bars = []
        for r in db.query(M.StageRecord).filter_by(heat_id=h.id):
            if r.started_at:
                bars.append({"stage": r.stage, "title": STAGE_TITLES[r.stage],
                             "start": r.started_at.isoformat(sep=" "),
                             "end": (r.submitted_at or m.at).isoformat(sep=" "),
                             "done": r.status in ("submitted", "approved")})
        gantt.append({"heat_no": h.heat_no, "status": h.status,
                      "grade": h.grade.code if h.grade else "", "bars": sorted(bars, key=lambda b: b["start"])})

    # heat timeline strip (Digital DNA) per active heat
    strips = [_strip(db, h) for h in active_all]

    # shift statistics
    sh_start, sh_end = m.shift_start, m.shift_end
    shift_heats = [h for h in heats_today if h.shift == m.shift]

    # recent activities (last 20 events)
    recent = (db.query(M.HeatEvent).order_by(M.HeatEvent.at.desc()).limit(20).all())

    delay_min_today = (db.query(func.coalesce(func.sum(M.Delay.duration_min), 0.0))
                       .join(M.Heat, M.Delay.heat_id == M.Heat.id)
                       .filter(M.Heat.plant_day == m.plant_day,
                               M.Delay.superseded == False).scalar())  # noqa

    return {
        "plant": {"plant_day": m.plant_day.isoformat(), "shift": m.shift,
                  "now": m.at.isoformat(sep=" "),
                  "shift_window": [sh_start.isoformat(sep=" "), sh_end.isoformat(sep=" ")],
                  "day_window": [day_start.isoformat(sep=" "), day_end.isoformat(sep=" ")]},
        "kpis": {"active_heats": len(active_all), "completed_today": len(completed),
                 "heats_today": len(heats_today),
                 "billets": int(tot_billets), "billet_t": round(tot_billet_t, 1),
                 "energy_kwh": round(tot_energy, 0), "o2_nm3": round(tot_o2, 0),
                 "delay_min": round(delay_min_today or 0, 0),
                 "shift_heats": len(shift_heats)},
        "attention": {
            "pending_approvals": pending_approvals,
            "open_delays": [{"heat_no": db.get(M.Heat, d.heat_id).heat_no,
                             "stage": STAGE_TITLES[d.stage],
                             "since": d.start_time.isoformat(sep=" "),
                             "reason": f"{d.reason.category} — {d.reason.reason}" if d.reason else "",
                             "remarks": d.remarks} for d in open_delays],
            "route_pending": [h.heat_no for h in active_all if h.route is None
                              and h.status in ("lrf", "route_selected")],
        },
        "active_heats": strips,
        "gantt": gantt,
        "recent": [{"at": e.at.isoformat(sep=" "), "kind": e.kind,
                    "heat_no": e.heat_no, "actor": e.actor_name,
                    "detail": e.detail} for e in recent],
    }


def _strip(db, h: M.Heat):
    gates = L.stage_gates(db, h)
    from ..engines.stage_config import required_stages
    steps = []
    recs = {r.stage: r for r in db.query(M.StageRecord).filter_by(heat_id=h.id)}
    for st in L.heat_required_stages(db, h):
        g = gates.get(st, {})
        rec = recs.get(st)
        state = ("done" if rec and rec.status in ("submitted", "approved")
                 else "running" if rec and rec.status == "in_progress"
                 else "open" if g.get("state") == "open" else "pending")
        steps.append({"stage": st, "title": STAGE_TITLES[st], "state": state})
    if h.status == "locked":
        for s in steps:
            s["state"] = "done"
    return {"heat_no": h.heat_no, "grade": h.grade.code if h.grade else "",
            "status": h.status, "route": h.route, "shift": h.shift,
            "plant_day": h.plant_day.isoformat(),
            "shift_engineer": h.shift_engineer, "steps": steps,
            "is_training": h.is_training}


@router.get("/notifications")
def notifications(unread_only: bool = False, q: str = "", kind: str = "",
                  offset: int = 0, limit: int = 60,
                  user: M.User = Depends(get_current_user),
                  db: DBSession = Depends(get_db)):
    """NOTIFICATION LAW: scales to 1000+ — server-side search, kind filter and
    windowed paging; nothing is ever hidden or lost (No-Delete: archive only)."""
    limit = max(1, min(int(limit or 60), 200))
    offset = max(0, int(offset or 0))
    base = db.query(M.Notification).filter(M.Notification.archived == False)  # noqa
    if q.strip():
        like = f"%{q.strip()}%"
        base = base.filter((M.Notification.title.ilike(like)) |
                           (M.Notification.body.ilike(like)) |
                           (M.Notification.heat_no.ilike(like)))
    if kind.strip():
        base = base.filter(M.Notification.kind == kind.strip())
    rows = (base.order_by(M.Notification.at.desc())
            .offset(offset).limit(limit + 1).all())
    has_more = len(rows) > limit
    q = rows[:limit]
    # NOTIFICATION PREFERENCE ENGINE: optional kinds honour user preferences;
    # MANDATORY kinds (emergency/security/system/delay) and critical priority
    # are NEVER filtered — no user can silence production-critical alerts.
    MANDATORY_KINDS = {"emergency", "security", "system", "delay"}
    prefs = user.notification_prefs or {}
    out = []
    for n in q:
        if n.audience_role and n.audience_role not in (user.role.name, "Administrator") \
           and user.role.name != "Administrator":
            continue
        if (n.kind not in MANDATORY_KINDS and n.priority != "critical"
                and prefs.get(n.kind, True) is False):
            continue
        read = user.id in (n.read_by or [])
        if unread_only and read:
            continue
        out.append({"id": n.id, "at": n.at.isoformat(sep=" "), "kind": n.kind,
                    "priority": n.priority, "title": n.title, "body": n.body,
                    "heat_no": n.heat_no, "read": read})
    return {"items": out, "offset": offset, "has_more": has_more}


@router.post("/notifications/read-all")
def notifications_read_all(user: M.User = Depends(get_current_user),
                           db: DBSession = Depends(get_db)):
    """Mark all currently visible notifications as read for this user."""
    n_marked = 0
    for n in (db.query(M.Notification)
              .filter(M.Notification.archived == False)          # noqa: E712
              .order_by(M.Notification.at.desc()).limit(1000)):
        rb = list(n.read_by or [])
        if user.id not in rb:
            rb.append(user.id)
            n.read_by = rb
            n_marked += 1
    db.commit()
    return {"ok": True, "marked": n_marked}


@router.post("/notifications/{nid}/read")
def mark_read(nid: int, user: M.User = Depends(get_current_user),
              db: DBSession = Depends(get_db)):
    n = db.get(M.Notification, nid)
    if n and user.id not in (n.read_by or []):
        n.read_by = (n.read_by or []) + [user.id]
        db.commit()
    return {"ok": True}


@router.get("/handover")
def shift_handover(user: M.User = Depends(get_current_user),
                   db: DBSession = Depends(get_db)):
    """Shift Intelligence (7.12): outgoing-shift summary for handover."""
    m = plant_time.now_moment()
    prev_shift, cur = plant_time.shift_handover_pairs(m)
    pd = m.plant_day if prev_shift != "B" or cur != "C" else m.plant_day - timedelta(days=1)
    try:
        win = plant_time.shift_window(pd, prev_shift)
    except ValueError:
        win = (m.shift_start, m.shift_end)
    evs = (db.query(M.HeatEvent)
           .filter(M.HeatEvent.at >= win[0], M.HeatEvent.at < win[1])
           .order_by(M.HeatEvent.at).all())
    submitted = [e for e in evs if e.kind == "stage_submitted"]
    locked = [e for e in evs if e.kind == "locked"]
    pend = (db.query(M.Heat)
            .filter(M.Heat.status.notin_(["locked", "aborted"]),
                    M.Heat.is_training == False,   # noqa
                    M.Heat.archived == False).all())  # noqa
    return {"outgoing_shift": prev_shift, "incoming_shift": cur,
            "window": [win[0].isoformat(sep=" "), win[1].isoformat(sep=" ")],
            "stages_submitted": len(submitted),
            "heats_locked": [e.heat_no for e in locked],
            "pending_heats": [{"heat_no": h.heat_no, "status": h.status,
                               "route": h.route} for h in pend],
            "events": [{"at": e.at.isoformat(sep=" "), "kind": e.kind,
                        "heat_no": e.heat_no, "detail": e.detail} for e in evs][-40:]}


# ══════════════════ SMART SWITCHABLE INDUSTRIAL GANTT CHART v2 ══════════════════
# THE HEART OF SMS-2 (Constitution S.4). Five switchable modes:
#   heat · machine · shift · delays · production   — each VS TIME.
# Machine lanes per Constitution: HM · EAF · ZPF · LRF-5 · LRF-6 · VD-2 · VD-3 ·
# LTC-1 · LTC-2 · BC-2 · BC-3 (+ Quality Lab). Digital Time Machine windows:
# any plant day, 1–31 day spans. Every module feeds it.

GANTT_MACHINES = [
    ("HM-BAY", "Hot Metal Bay", ["eaf"]),   # charge window, from the EAF record
    ("EAF-1", "Electric Arc Furnace", ["eaf"]),
    ("ZPF-1", "ZPF", ["zpf"]),
    ("LRF-5", "Ladle Refining Furnace 5", ["lrf"]),
    ("LRF-6", "Ladle Refining Furnace 6", ["lrf"]),
    ("VD-2", "Vacuum Degasser 2", ["vd"]),
    ("VD-3", "Vacuum Degasser 3", ["vd"]),
    ("LTC-1", "Ladle Transfer Car 1", []),
    ("LTC-2", "Ladle Transfer Car 2", []),
    ("BC-2", "Billet Caster 2", ["caster"]),
    ("BC-3", "Billet Caster 3", ["caster"]),
    ("QLAB", "Quality Laboratory", ["quality"]),
]
MACHINE_CODES = [m[0] for m in GANTT_MACHINES]
SHUTDOWN_MIN = 60.0

# The Gantt draws PROCESS reality, not data-entry reality: bars prefer the
# operator-entered process timestamps and fall back to record timestamps
# only when those are absent (constitution: nothing without a timeline —
# and the timeline is the plant's, not the keyboard's).
STAGE_TIME_FIELDS = {
    "eaf": ("charging_start", "tap_end"),
    "zpf": ("zpf_start", "zpf_end"),
    "lrf": ("lrf_start", "lrf_end"),
    "vd": ("vd_start", "vd_end"),
    "caster": ("cast_start", "cast_end"),
}


# The hot metal bay's occupancy lives inside the EAF record now that charging
# is an EAF operation. Machine mode still needs it as its own lane: the bay is
# physically busy from ladle arrival until the charge goes in.
CHARGE_TIME_FIELDS = ("hm_arrival", "hm_charging")


def _proc_dt(v):
    """Parse an operator-entered datetime-local value; None if absent/invalid."""
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


def _machine_for(stage: str, heat: M.Heat, data: dict | None = None) -> str | None:
    data = data or {}
    if stage == "eaf":
        return "EAF-1"
    if stage == "zpf":
        return "ZPF-1"
    if stage == "lrf":
        rr = heat.route_refining if heat.route_refining in ("LRF-5", "LRF-6") else None
        return data.get("lrf_unit") or rr or "LRF-5"
    if stage == "vd":
        return heat.route_vd or ("VD-2" if (heat.route or "").startswith("VD") else None)
    if stage == "caster":
        if heat.route in ("BC-2", "BC-3"):
            return heat.route
        return heat.route_caster or "BC-2"
    if stage == "quality":
        return "QLAB"
    return None


def _collect_window(db, w0, w1, now_at, include_locked=True):
    """Fuse every module's data intersecting [w0,w1] — the single feed."""
    # No created_at cutoff: a heat's bars live on the plant day of its
    # PROCESS times, which back-entry can place before the record existed.
    # The per-bar window test below is the only inclusion rule.
    heats = (db.query(M.Heat)
             .filter(M.Heat.archived == False,          # noqa: E712
                     M.Heat.is_training == False).all())  # noqa: E712
    picked = []
    for h in heats:
        recs = db.query(M.StageRecord).filter_by(heat_id=h.id).all()
        bars = []; charge_bars = []
        for r in recs:
            if not r.started_at:
                continue
            d = r.data or {}
            live = r.status == "in_progress" and h.status not in ("locked", "aborted")
            b0 = r.started_at
            b1 = r.submitted_at or (now_at if h.status not in ("locked", "aborted") else r.started_at)
            # Prefer the plant's own clock: operator-entered process times.
            f0, f1 = STAGE_TIME_FIELDS.get(r.stage, (None, None))
            p0, p1 = _proc_dt(d.get(f0)) if f0 else None, _proc_dt(d.get(f1)) if f1 else None
            if p0:
                b0 = p0
                b1 = now_at if live else max(b1, p0)
            if p1:
                b1 = p1
                if not p0:
                    b0 = min(b0, p1)
            if b1 < b0:
                b1 = b0
            if b1 < w0 or b0 > w1:
                continue
            bar = {"heat_no": h.heat_no, "stage": r.stage,
                   "title": STAGE_TITLES[r.stage],
                   "machine": _machine_for(r.stage, h, d),
                   "start": max(b0, w0).isoformat(sep=" "),
                   "end": min(b1, w1).isoformat(sep=" "),
                   "done": r.status in ("submitted", "approved"),
                   "live": live,
                   "grade": h.grade.code if h.grade else "", "route": h.route,
                   "shift_at_start": plant_time.resolve(max(b0, w0)).shift}
            if r.stage == "caster":
                bar["billet_t"] = float(d.get("billet_weight") or 0)
                bar["ltc"] = d.get("ltc_no")
            bars.append(bar)
            # Charge window → HM-BAY lane. Kept OUT of `bars` so heat, shift
            # and production modes still see exactly one EAF bar per heat;
            # only the machine board consumes it.
            if r.stage == "eaf":
                c0 = _proc_dt(d.get(CHARGE_TIME_FIELDS[0]))
                c1 = _proc_dt(d.get(CHARGE_TIME_FIELDS[1]))
                if c0 and c1 and c1 >= c0 and not (c1 < w0 or c0 > w1):
                    charge_bars.append({**bar, "machine": "HM-BAY",
                                        "kind": "charge",
                                        "title": "Hot Metal Charge",
                                        "start": max(c0, w0).isoformat(sep=" "),
                                        "end": min(c1, w1).isoformat(sep=" "),
                                        "live": False, "done": bar["done"],
                                        "shift_at_start": plant_time.resolve(max(c0, w0)).shift})
        if not bars or (h.status == "locked" and not include_locked):
            continue
        bars.sort(key=lambda b: b["start"])
        delays = []
        for dl in db.query(M.Delay).filter_by(heat_id=h.id, superseded=False):
            d0, d1 = dl.start_time, (dl.end_time or now_at)
            if d1 < w0 or d0 > w1:
                continue
            dur = dl.duration_min or ((now_at - dl.start_time).total_seconds() / 60.0)
            rec_d = next((r.data or {} for r in recs if r.stage == dl.stage), {})
            delays.append({"heat_no": h.heat_no, "stage": dl.stage,
                           "machine": _machine_for(dl.stage, h, rec_d),
                           "start": max(d0, w0).isoformat(sep=" "),
                           "end": min(d1, w1).isoformat(sep=" "),
                           "open": dl.end_time is None, "min": round(dur, 0),
                           "shutdown": dur >= SHUTDOWN_MIN,
                           "category": dl.reason.category if dl.reason else "Delay",
                           "reason": dl.reason.reason if dl.reason else dl.remarks,
                           "shift_at_start": plant_time.resolve(max(d0, w0)).shift})
        events = []
        for e in (db.query(M.HeatEvent)
                  .filter(M.HeatEvent.heat_id == h.id,
                          M.HeatEvent.at >= w0, M.HeatEvent.at <= w1,
                          M.HeatEvent.kind.in_(["route_selected", "approval", "locked",
                                                "unlocked", "aborted", "validation",
                                                "correction"]))):
            if e.kind == "validation" and "0 error" in (e.detail or ""):
                continue
            rec_d = next((r.data or {} for r in recs if r.stage == e.stage), {}) if e.stage else {}
            events.append({"heat_no": h.heat_no, "at": e.at.isoformat(sep=" "),
                           "kind": e.kind, "stage": e.stage,
                           "machine": _machine_for(e.stage, h, rec_d) if e.stage else None,
                           "detail": (e.detail or "")[:140]})
        picked.append({"heat": h, "bars": bars, "charge_bars": charge_bars,
                       "delays": delays, "events": events})
    return picked


@router.get("/gantt")
def smart_gantt(mode: str = "heat", span: str = "day", plant_day: str | None = None,
                days: int = 1, include_locked: bool = True,
                user: M.User = Depends(get_current_user),
                db: DBSession = Depends(get_db)):
    """Smart Switchable Industrial Gantt Chart.
    mode: heat | machine | shift | delays | production
    span: day | shift ('shift' applies to the current plant day only)
    plant_day + days: Digital Time Machine — any historical window, 1–31 days."""
    from datetime import date as _date
    m = plant_time.now_moment()
    pd = _date.fromisoformat(plant_day) if plant_day else m.plant_day
    days = max(1, min(int(days or 1), 31))
    if span == "shift" and pd == m.plant_day and days == 1:
        w0, w1 = m.shift_start, m.shift_end
    else:
        w0 = plant_time.plant_day_window(pd - timedelta(days=days - 1))[0]
        w1 = plant_time.plant_day_window(pd)[1]

    picked = _collect_window(db, w0, w1, m.at, include_locked)

    rows = []
    if mode == "machine":
        for code, label, _stages in GANTT_MACHINES:
            bars = [b for p in picked
                    for b in p["bars"] + p["charge_bars"] if b["machine"] == code]
            # LTC lanes: represented by casting windows of heats that used the car
            if code.startswith("LTC"):
                bars = [dict(b, title=f"{b['title']} transfer") for p in picked
                        for b in p["bars"] if b["stage"] == "caster" and b.get("ltc") == code]
            delays = [d for p in picked for d in p["delays"] if d["machine"] == code]
            events = [e for p in picked for e in p["events"] if e["machine"] == code]
            # MACHINE MODE SHOWS THE WHOLE PLANT — every lane, always.
            # VD, LTC and ZPF lanes used to be dropped when no heat had used
            # them in the window. That optimised away real information: on a
            # machine board an EMPTY LANE IS A FACT — that unit is idle and
            # available. Hiding it makes the plant look smaller than it is and
            # leaves the operator unable to tell "idle" from "not there".
            # (Heat mode is unaffected: a ZPF-bypassed heat still shows no ZPF
            # bar in its own row, which is the Process Route Law.)
            util = round(sum((plant_time.duration_minutes(
                datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) or 0)
                for b in bars), 0)
            rows.append({"key": code, "label": code, "sub": label, "kind": "machine",
                         "bars": sorted(bars, key=lambda b: b["start"]),
                         "delays": delays, "events": events, "utilization_min": util})
    elif mode == "shift":
        for sh in ["C", "A", "B"]:
            bars = [b for p in picked for b in p["bars"] if b["shift_at_start"] == sh]
            delays = [d for p in picked for d in p["delays"] if d["shift_at_start"] == sh]
            events = [e for p in picked for e in p["events"]
                      if plant_time.resolve(datetime.fromisoformat(e["at"])).shift == sh]
            rows.append({"key": f"SHIFT-{sh}", "label": f"Shift {sh}",
                         "sub": f"{len({b['heat_no'] for b in bars})} heats · "
                                f"{round(sum(d['min'] for d in delays), 0)} delay min",
                         "kind": "shift", "bars": sorted(bars, key=lambda b: b["start"]),
                         "delays": delays, "events": events})
    elif mode == "delays":
        cats = sorted({d["category"] for p in picked for d in p["delays"]})
        for cat in cats:
            delays = [d for p in picked for d in p["delays"] if d["category"] == cat]
            rows.append({"key": f"DL-{cat}", "label": cat,
                         "sub": f"{len(delays)} delay(s) · {round(sum(d['min'] for d in delays), 0)} min",
                         "kind": "delays", "bars": [], "delays": delays, "events": []})
        if not cats:
            rows = []
    elif mode == "production":
        for code in ["BC-2", "BC-3"]:
            bars = [dict(b, title=f"{b['heat_no']} · {b.get('billet_t', 0):g} t")
                    for p in picked for b in p["bars"]
                    if b["stage"] == "caster" and b["machine"] == code]
            tonnes = round(sum(b.get("billet_t", 0) for b in bars), 1)
            delays = [d for p in picked for d in p["delays"]
                      if d["stage"] == "caster" and d["machine"] == code]
            rows.append({"key": code, "label": code,
                         "sub": f"{len(bars)} heats cast · {tonnes} t billets",
                         "kind": "production", "bars": bars, "delays": delays,
                         "events": [], "tonnes": tonnes})
    else:  # heat mode (default)
        for p in sorted(picked, key=lambda x: x["bars"][0]["start"]):
            h = p["heat"]
            rows.append({"key": h.heat_no, "label": h.heat_no,
                         "sub": f"{h.grade.code if h.grade else ''} · {h.route or 'route pending'} · {h.status.replace('_', ' ')}",
                         "kind": "heat", "status": h.status,
                         "bars": p["bars"], "delays": p["delays"], "events": p["events"]})

    total_delay = round(sum(d["min"] for r in rows for d in r["delays"]), 0)
    shutdowns = sum(1 for r in rows for d in r["delays"] if d["shutdown"])
    live_bars = sum(1 for r in rows for b in r["bars"] if b["live"])
    tonnes = round(sum(b.get("billet_t", 0) for r in rows for b in r["bars"]
                       if b.get("billet_t")), 1)
    return {"mode": mode, "span": span, "days": days,
            "window": [w0.isoformat(sep=" "), w1.isoformat(sep=" ")],
            "now": m.at.isoformat(sep=" "),
            "plant_day": pd.isoformat(), "shift": m.shift,
            "machines": MACHINE_CODES, "rows": rows,
            "summary": {"rows": len(rows), "live": live_bars,
                        "delay_min": total_delay, "shutdowns": shutdowns,
                        "tonnes": tonnes}}


# ══════════════ LIVE MACHINE INTELLIGENCE (Constitution S.4/S.5) ══════════════

@router.get("/machines")
def machine_board(user: M.User = Depends(get_current_user),
                  db: DBSession = Depends(get_db)):
    """Every machine, live: status · current heat · running minutes · shift ·
    delays · utilization · production · next heat."""
    m = plant_time.now_moment()
    w0, w1 = plant_time.plant_day_window(m.plant_day)
    window_min = (min(w1, m.at) - w0).total_seconds() / 60.0 or 1.0
    picked = _collect_window(db, w0, w1, m.at, include_locked=True)
    eq_status = {e.code: e.status for e in db.query(M.Equipment)}

    # next heat per machine: earliest active heat whose next open stage maps there
    from ..engines.stage_config import required_stages
    next_heat = {}
    for h in (db.query(M.Heat)
              .filter(M.Heat.status.notin_(["locked", "aborted"]),
                      M.Heat.is_training == False,        # noqa: E712
                      M.Heat.archived == False)           # noqa: E712
              .order_by(M.Heat.created_at)):
        gates = L.stage_gates(db, h)
        nxt = next((st for st in L.heat_required_stages(db, h)
                    if gates.get(st, {}).get("state") == "open"), None)
        if nxt:
            rec = db.query(M.StageRecord).filter_by(heat_id=h.id, stage=nxt).first()
            mc = _machine_for(nxt, h, (rec.data or {}) if rec else {})
            if mc and mc not in next_heat and (not rec or rec.status == "pending"):
                next_heat[mc] = h.heat_no

    board = []
    for code, label, _stages in GANTT_MACHINES:
        bars = [b for p in picked
                for b in p["bars"] + p["charge_bars"] if b["machine"] == code]
        delays = [d for p in picked for d in p["delays"] if d["machine"] == code]
        open_delay = next((d for d in delays if d["open"]), None)
        live = next((b for b in bars if b["live"]), None)
        declared = eq_status.get(code, "available")
        if declared in ("maintenance", "offline"):
            state = declared.upper()
        elif open_delay:
            state = "DELAYED"
        elif live:
            state = "RUNNING"
        elif next_heat.get(code):
            state = "READY"
        else:
            state = "IDLE"
        util_min = sum((plant_time.duration_minutes(
            datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) or 0)
            for b in bars)
        run_min = None
        if live:
            run_min = round((m.at - datetime.fromisoformat(live["start"])).total_seconds() / 60.0, 0)
        prod_t = round(sum(b.get("billet_t", 0) for b in bars), 1) if code.startswith("BC") else None
        board.append({
            "code": code, "label": label, "state": state,
            "declared": declared,
            "current_heat": live and live["heat_no"],
            "current_stage": live and live["title"],
            "running_min": run_min,
            "shift": m.shift,
            "delay_count": len(delays),
            "delay_min": round(sum(d["min"] for d in delays), 0),
            "open_delay": open_delay and f"{open_delay['category']}: {open_delay['reason']}",
            "utilization_pct": round(min(util_min / window_min * 100, 100), 1),
            "production_t": prod_t,
            "next_heat": next_heat.get(code),
        })
    return {"plant_day": m.plant_day.isoformat(), "shift": m.shift,
            "now": m.at.isoformat(sep=" "), "machines": board}


# ═══════════════════════════════════════════════════════════════════════
# THE REAL-TIME LAW — 2-second PULSE
# A deliberately tiny endpoint: counters and short signatures only, never
# payloads. The client compares each domain's signature with the previous
# one and refreshes ONLY the component whose signature changed. The page
# itself never reloads and auto-save is never interrupted.
# ═══════════════════════════════════════════════════════════════════════

def _sig(*parts) -> str:
    import hashlib
    return hashlib.blake2s("|".join(str(p) for p in parts).encode(),
                           digest_size=8).hexdigest()


@router.get("/pulse")
def pulse(user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    m = plant_time.now_moment()
    role = user.role.name

    # ── notifications (per user: unread count + newest id) ──
    n_max = db.query(func.max(M.Notification.id)).filter(
        M.Notification.archived == False).scalar() or 0          # noqa: E712
    unread = 0
    for n in (db.query(M.Notification)
              .filter(M.Notification.archived == False)          # noqa: E712
              .order_by(M.Notification.id.desc()).limit(200)):
        if n.audience_role and n.audience_role not in (role, "Administrator") \
           and role != "Administrator":
            continue
        if user.id not in (n.read_by or []):
            unread += 1

    # ── approvals awaiting a signature ──
    appr = []
    for h in db.query(M.Heat).filter(M.Heat.status == "review",
                                     M.Heat.is_training == False,   # noqa: E712
                                     M.Heat.archived == False):     # noqa: E712
        levels = {a.level for a in db.query(M.Approval)
                  .filter_by(heat_id=h.id, scope="final", decision="approved")}
        nxt = next((c for c in L.APPROVAL_CHAIN if c["level"] not in levels), None)
        if nxt:
            appr.append((h.heat_no, nxt["level"], nxt["role"]))
    mine = sum(1 for _, _, r in appr if r == role or role == "Administrator")

    # ── heat + production status (plant day) ──
    heats = (db.query(M.Heat.heat_no, M.Heat.status, M.Heat.route,
                      M.Heat.route_refining, M.Heat.locked_at)
             .filter(M.Heat.plant_day == m.plant_day).all())
    tonnes = db.query(func.sum(M.StageRecord.data["tap_weight"].as_float())).filter(
        M.StageRecord.plant_day == m.plant_day, M.StageRecord.stage == "eaf").scalar() or 0

    # ── machines, reports, users, quality ──
    mach = db.query(M.Equipment.code, M.Equipment.status).order_by(M.Equipment.code).all()
    rep_max = db.query(func.max(M.ReportArchive.id)).scalar() or 0
    rep_n = db.query(M.ReportArchive).count()
    usr = db.query(M.User.employee_id, M.User.active, M.User.archived,
                   M.User.role_id).order_by(M.User.employee_id).all()
    pw_pending = db.query(M.PasswordResetRequest).filter_by(status="pending").count()
    qsamp = (db.query(M.ChemistrySample).join(M.Heat, M.ChemistrySample.heat_id == M.Heat.id)
             .filter(M.ChemistrySample.superseded == False,       # noqa: E712
                     M.Heat.plant_day == m.plant_day).count())
    corr = db.query(M.CorrectionRequest).filter_by(status="pending").count()

    return {
        "t": m.at.isoformat(sep=" ", timespec="seconds"),
        "plant_day": m.plant_day.isoformat(), "shift": m.shift,
        "notifications": {"unread": unread, "max_id": n_max},
        "approvals": {"pending": len(appr), "mine": mine,
                      "corrections": corr, "password_requests": pw_pending,
                      "sig": _sig(appr, corr, pw_pending)},
        "heats": {"active": len(heats), "sig": _sig(heats)},
        "production": {"heats": len(heats), "tonnes": round(float(tonnes), 1)},
        "machines": {"sig": _sig(mach)},
        "reports": {"count": rep_n, "max_id": rep_max},
        "users": {"count": len(usr), "sig": _sig(usr)},
        "quality": {"samples": qsamp, "sig": _sig(qsamp)},
    }
