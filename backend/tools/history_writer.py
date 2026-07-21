"""
SMS-2 IDOS — write plant history into the database
===================================================
The DB half of the history import, deliberately split from the spreadsheet
half: this module never touches Excel and never imports openpyxl, so it runs
on a hosted server where neither the workbook nor that library exists.

Two callers share it:
  tools/import_history.py  — reads IDOS.xlsx on a developer machine
  tools/seed_history.py    — replays the committed bundle on the live server

WHY THIS WRITES STRAIGHT TO THE DATABASE
    The API enforces stage gates, the three-level approval chain and blocking
    validation — it models an operator working a live heat. A backfill is the
    opposite: the heats are finished, arrive out of order and are incomplete.
    Through the API they would be rejected, or would fabricate an approval
    chain that never happened.

INTEGRITY
    Every heat written here is flagged `is_imported`, marked locked, carries an
    `imported` event naming its source, and is given NO approval signatures.
    The app then declines to judge it against current entry rules, and says so
    on screen rather than implying it passed them.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta

from app import models as M
from app.core import plant_time
from app.engines.validation import auto_calculations
from tools import history_map as HM

# The process window each stage occupies. Several candidates per end because
# the workbook does not fill the same column for every heat — the EAF sheet
# records power-on rather than a charging start, for instance. First match
# wins, so a record spans real time instead of collapsing to a point.
STAGE_SPAN = {
    "eaf":    (("charging_start", "power_on", "hm_charging", "hm_arrival"),
               ("tap_end", "tap_start")),
    "zpf":    (("zpf_start", "blow_on_time", "hm_charging"),
               ("tap_time", "zpf_end")),
    "lrf":    (("lrf_start", "power_on", "eaf_tap_time"), ("lrf_end", "power_off")),
    "vd":     (("vd_start", "vac_achieved"), ("vd_end",)),
    "caster": (("cast_start", "ladle_on_turret"), ("cast_end",)),
}

BUNDLE_VERSION = 1


# ── (de)serialisation for the portable bundle ─────────────────────────────

def _enc(o):
    if isinstance(o, (datetime, date, time)):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {type(o)}")


def to_bundle(heats: dict, source: str) -> str:
    """Serialise the gathered heats into the portable JSON bundle."""
    return json.dumps({"version": BUNDLE_VERSION, "source": source,
                       "generated": datetime.now().isoformat(timespec="seconds"),
                       "heats": heats}, default=_enc)


def from_bundle(raw: str) -> tuple[dict, dict]:
    """Parse a bundle back into (heats, meta), restoring dates and times."""
    obj = json.loads(raw)
    if obj.get("version") != BUNDLE_VERSION:
        raise ValueError(f"bundle version {obj.get('version')} — this build expects "
                         f"{BUNDLE_VERSION}. Regenerate it with import_history.py --export.")
    heats = obj["heats"]
    for h in heats.values():
        h["day"] = date.fromisoformat(h["day"]) if h.get("day") else None
        for d in h.get("delays", []):
            d["day"] = date.fromisoformat(d["day"]) if d.get("day") else None
            for k in ("start", "end"):
                d[k] = time.fromisoformat(d[k]) if d.get(k) else None
    meta = {k: v for k, v in obj.items() if k != "heats"}
    return heats, meta


# ── helpers ───────────────────────────────────────────────────────────────

def dt_of(day, t):
    """Combine a PLANT day with a clock time into a real timestamp.

    The sheets record the plant day and a wall clock, and the plant day runs
    22:00 on (D-1) → 21:59 on D — C shift opens it. So a 23:41 reading on
    plant day 30-Jun happened on calendar 29-Jun, while 01:03 on the same
    plant day is 30-Jun. Combining naively puts a heat's tapping BEFORE its
    own charging and scatters the Gantt across the wrong days.
    """
    if day is None or t is None:
        return None
    if t >= plant_time.PLANT_DAY_START:
        return datetime.combine(day - timedelta(days=1), t)
    return datetime.combine(day, t)


def _parse_iso(v):
    try:
        return datetime.fromisoformat(str(v)) if v else None
    except (TypeError, ValueError):
        return None


def _first_time(data, keys):
    for k in keys:
        t = _parse_iso(data.get(k))
        if t:
            return t
    return None


# ── the writer ────────────────────────────────────────────────────────────

class HistoryWriter:
    def __init__(self, db):
        self.db = db
        self.stats = Counter()
        self.unknown_mat = Counter()
        self.unknown_agency = Counter()
        self.materials = {m.code: m for m in db.query(M.Material)}
        self.grades = {g.code.upper(): g for g in db.query(M.SteelGrade)}
        self.reasons = defaultdict(dict)
        self.by_agency = defaultdict(list)
        for r in db.query(M.DelayReason):
            self.reasons[r.category][r.reason.strip().lower()] = r
            self.by_agency[r.category].append(r)
        self.user = (db.query(M.User).filter_by(employee_id="ADMIN").first()
                     or db.query(M.User).first())

    def resolve_grade(self, code):
        if not code:
            return None
        g = self.grades.get(code.upper())
        if not g:
            g = M.SteelGrade(code=code, name=code)
            self.db.add(g)
            self.db.flush()
            self.grades[code.upper()] = g
            self.stats["grades created"] += 1
        return g

    def resolve_reason(self, agency, code):
        pool = self.by_agency.get(agency)
        if not pool:
            self.unknown_agency[agency] += 1
            return None
        if code:
            hit = self.reasons[agency].get(code.strip().lower())
            if hit:
                return hit
            low = code.strip().lower()
            for r in pool:
                if low in r.reason.lower() or r.reason.lower() in low:
                    return r
        return pool[0]

    def write(self, heats: dict, source: str = "history bundle") -> int:
        existing = {h.heat_no for h in self.db.query(M.Heat.heat_no)}
        written = 0
        for hno, h in sorted(heats.items()):
            if hno in existing:
                self.stats["heats skipped (already present)"] += 1
                continue
            if not h.get("day"):
                self.stats["heats skipped (no date)"] += 1
                continue
            day = h["day"]
            moment = plant_time.resolve(datetime.combine(day, datetime.min.time()))
            grade = self.resolve_grade(h.get("grade"))
            heat = M.Heat(heat_no=hno, grade_id=grade.id if grade else None,
                          status="locked", entry_stage=h["entry"],
                          is_imported=True,
                          plant_day=day, shift=(h.get("shift") or moment.shift),
                          shift_engineer=(h["data"].get("lrf") or {}).get("shift_incharge", ""),
                          created_by=self.user.id if self.user else None,
                          locked_at=datetime.combine(day, datetime.min.time()))
            self.db.add(heat)
            self.db.flush()

            mats_by_stage = defaultdict(list)
            for stage, code, qty, point in h.get("mats", []):
                mat = self.materials.get(code)
                if not mat:
                    self.unknown_mat[code] += 1
                    continue
                self.db.add(M.MaterialAddition(
                    heat_id=heat.id, stage=stage, material_id=mat.id,
                    quantity=qty, unit=mat.unit, addition_point=point,
                    remarks="imported"))
                mats_by_stage[stage].append({"quantity": qty, "category": mat.category,
                                             "point": point})
                self.stats["material additions"] += 1

            stages = {"lrf", "caster", "quality", "review", h["entry"]}
            stages |= {s for s in h["data"] if s in
                       ("eaf", "zpf", "lrf", "vd", "caster")}
            if h["entry"] == "zpf":
                stages.discard("eaf")

            # Machine identifiers normalised onto the codes the Gantt and the
            # route law use — the sheets write a bare 5 for the LRF and 2.1 for
            # the VD tank, which match no lane.
            data_by_stage = {s: dict(h["data"].get(s, {})) for s in stages}
            lrf_unit = HM.canon_unit(data_by_stage.get("lrf", {}).get("lrf_unit"), "lrf")
            vd_tank = HM.canon_unit(data_by_stage.get("vd", {}).get("vd_tank"), "vd")
            ccm = HM.canon_unit(data_by_stage.get("caster", {}).get("ccm_no"), "ccm")
            if lrf_unit:
                data_by_stage["lrf"]["lrf_unit"] = lrf_unit
            if vd_tank:
                data_by_stage["vd"]["vd_tank"] = vd_tank
            if ccm:
                data_by_stage["caster"]["ccm_no"] = ccm
            # A heat that was degassed is on a VD route; otherwise it went from
            # the LRF straight to its caster.
            heat.route_refining = lrf_unit
            heat.route_vd = vd_tank
            heat.route_caster = ccm
            heat.route = vd_tank or ccm

            for stage in sorted(stages):
                data = data_by_stage.get(stage, {})
                if stage in ("eaf", "zpf", "lrf", "vd", "caster"):
                    data.update(auto_calculations(stage, data,
                                                  mats_by_stage.get(stage, [])))
                # The Gantt SKIPS any record without started_at, so a record
                # written without one is invisible on the plant's main board
                # however complete its data is.
                starts, ends = STAGE_SPAN.get(stage, ((), ()))
                started = _first_time(data, starts)
                ended = _first_time(data, ends)
                if data and not started:
                    started = ended or datetime.combine(day, datetime.min.time())
                if started and ended and ended < started:
                    ended = started
                self.db.add(M.StageRecord(
                    heat_id=heat.id, stage=stage, data=data,
                    status="submitted" if data else "pending",
                    plant_day=day, shift=(h.get("shift") or moment.shift),
                    started_at=started,
                    submitted_at=(ended or started) if data else None))

            for d in h.get("delays", []):
                reason = self.resolve_reason(d["agency"], d["code"])
                start = dt_of(d["day"], d["start"]) or datetime.combine(
                    d["day"], datetime.min.time())
                self.db.add(M.Delay(
                    heat_id=heat.id, stage=d["stage"], start_time=start,
                    end_time=dt_of(d["day"], d["end"]), duration_min=d["min"],
                    reason_id=reason.id if reason else None,
                    remarks=(d.get("desc") or d.get("code") or "")[:400]))
                self.stats["delays written"] += 1

            self.db.add(M.HeatEvent(
                heat_id=heat.id, heat_no=hno, kind="imported", at=heat.locked_at,
                plant_day=day, shift=heat.shift, actor_name="History Import",
                detail=f"Backfilled from {source} — melted in the "
                       f"{h['entry'].upper()}. Not operator-entered; carries no "
                       f"approval signatures."))
            written += 1
            self.stats["heats imported"] += 1
            if written % 50 == 0:
                self.db.flush()
        self.advance_series()
        return written

    def advance_series(self):
        """Move each heat-number series past the history just loaded.

        Backfilling real heats consumes real numbers. If a series still points
        inside the imported range, the very next heat an operator creates
        collides with a historical one and the insert fails on the unique
        heat_no constraint.
        """
        for s in self.db.query(M.HeatSeries):
            top = 0
            for (n,) in self.db.query(M.Heat.heat_no).filter(
                    M.Heat.heat_no.like(f"{s.prefix}%")):
                digits = n[len(s.prefix):]
                if digits.isdigit():
                    top = max(top, int(digits))
            if top >= s.next_number:
                self.stats[f"series {s.prefix} advanced to {top + 1}"] += 1
                s.next_number = top + 1
