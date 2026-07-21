"""
SMS-2 IDOS — import the plant's historical production workbook (IDOS.xlsx)
===========================================================================
Backfills real past production: ~657 heats across two melting furnaces, their
consumption, refining, casting and delay records.

    python tools/import_history.py "E:/Projects/Assets/IDOS.xlsx" --dry-run
    python tools/import_history.py "E:/Projects/Assets/IDOS.xlsx"

WHY THIS WRITES STRAIGHT TO THE DATABASE
    The API deliberately enforces stage gates, the three-level approval chain
    and blocking validation — it models an operator working a live heat. A
    historical backfill is the opposite: the heats are already finished, the
    rows arrive out of order and some are incomplete. Pushing them through the
    API would either be rejected or would fabricate an approval chain that
    never happened. (tools/seed_demo.py uses the API precisely because it is
    pretending to be operators; this is not.)

INTEGRITY
    Imported heats are NOT given signatures. Each is marked `locked`, carries
    an `imported` event naming the source file, and writes one audit entry —
    so the append-only trail shows plainly that this is backfilled history and
    not operator-entered work.

Safe to re-run: a heat number that already exists is skipped, never doubled.
Requires openpyxl (developer-only; the running application never reads .xlsx).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models as M                                    # noqa: E402
from app.core.database import SessionLocal, engine, Base       # noqa: E402
from app.core import plant_time                                # noqa: E402
from app.engines.validation import auto_calculations           # noqa: E402
from tools import history_map as HM                            # noqa: E402


# ── workbook reading ──────────────────────────────────────────────────────

def read_sheet(wb, name):
    """Yield {header: value} per data row, respecting multi-row headers.

    Date and Shift are written once per day and left blank on the rows that
    follow (merged cells in the original sheet), so they are carried forward.
    Without this, most heats would arrive with no date and be dropped.
    """
    hdr_row, data_row, last_col = HM.SHEETS[name]
    ws = wb[name]
    heads = {}
    for c in range(1, last_col + 1):
        h = HM.norm_head(ws.cell(row=hdr_row, column=c).value)
        if h:
            heads[c] = h
    carry = {}
    for r in range(data_row, ws.max_row + 1):
        row = {heads[c]: ws.cell(row=r, column=c).value for c in heads}
        if not any(v not in (None, "") for v in row.values()):
            continue
        for k in ("Date", "Shift"):
            if k in row:
                if row[k] not in (None, ""):
                    carry[k] = row[k]
                elif k in carry:
                    row[k] = carry[k]
        yield row


def heat_no_of(row):
    for k in ("Heat No.", "Heat No"):
        if k in row:
            n = HM.norm_heat(row[k])
            if n:
                return n
    return ""


# The process window each stage occupies. Several candidates per end because
# the workbook does not fill the same column for every heat — the EAF sheet
# records power-on rather than a charging start, for instance. First match
# wins, so a record still spans real time instead of collapsing to a point.
STAGE_SPAN = {
    "eaf":    (("charging_start", "power_on", "hm_charging", "hm_arrival"),
               ("tap_end", "tap_start")),
    "zpf":    (("zpf_start", "blow_on_time", "hm_charging"),
               ("tap_time", "zpf_end")),
    "lrf":    (("lrf_start", "power_on", "eaf_tap_time"), ("lrf_end", "power_off")),
    "vd":     (("vd_start", "vac_achieved"), ("vd_end",)),
    "caster": (("cast_start", "ladle_on_turret"), ("cast_end",)),
}


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


# ── mapping one row onto stage data ───────────────────────────────────────

def apply_fields(row, fmap, day, out, unmapped=None, seen=None):
    """Write mapped values into out[stage][key]; record unmapped headers."""
    for head, val in row.items():
        if seen is not None:
            seen.add(head)
        spec = fmap.get(head)
        if not spec:
            if unmapped is not None and head not in HM.IGNORE \
                    and head not in HM.MATERIALS and head not in HM.CASTER_LOSSES:
                unmapped[head] += 1
            continue
        stage, key, kind = spec
        if val in (None, ""):
            continue
        if kind == "num":
            v = HM.as_num(val)
        elif kind == "time":
            ts = dt_of(day, HM.as_time(val))
            v = ts.isoformat(timespec="minutes") if ts else None
        elif kind == "section":
            n = HM.as_num(val)
            v = f"{int(n)}×{int(n)}" if n else None
        else:
            v = HM.norm_head(val)
        if v not in (None, ""):
            out[stage][key] = v


def collect_materials(row, entry_stage, out_mats):
    """Turn consumable columns into (stage, code, qty, point) additions."""
    for head, val in row.items():
        spec = HM.MATERIALS.get(head)
        if not spec:
            continue
        qty = HM.as_num(val)
        if not qty or qty <= 0:
            continue
        code, hint, point = spec
        stage = entry_stage if hint == "entry" else hint
        out_mats.append((stage, code, qty, point))


# ── the import ────────────────────────────────────────────────────────────

class Importer:
    def __init__(self, db, wb, dry):
        self.db, self.wb, self.dry = db, wb, dry
        self.unmapped = Counter()
        self.agencies = Counter()
        self.unknown_mat = Counter()
        self.unknown_agency = Counter()
        self.stats = Counter()
        self.materials = {m.code: m for m in db.query(M.Material)}
        self.grades = {g.code.upper(): g for g in db.query(M.SteelGrade)}
        self.reasons = defaultdict(dict)     # agency -> {reason_lower: row}
        for r in db.query(M.DelayReason):
            self.reasons[r.category][r.reason.strip().lower()] = r
        self.by_agency = defaultdict(list)
        for r in db.query(M.DelayReason):
            self.by_agency[r.category].append(r)
        self.user = (db.query(M.User).filter_by(employee_id="ADMIN").first()
                     or db.query(M.User).first())
        # heat_no -> {"day":date, "entry":"eaf"|"zpf", "grade":str,
        #             "data":{stage:{...}}, "mats":[...], "delays":[...]}
        self.heats = {}

    # -- gather ---------------------------------------------------------
    def slot(self, hno, day=None, entry=None, grade=None, shift=None):
        h = self.heats.get(hno)
        if not h:
            h = {"day": None, "entry": entry or ("zpf" if hno.startswith("F") else "eaf"),
                 "grade": None, "shift": None, "data": defaultdict(dict),
                 "mats": [], "delays": []}
            self.heats[hno] = h
        if day and not h["day"]:
            h["day"] = day
        if grade and not h["grade"]:
            h["grade"] = grade
        if shift and not h["shift"]:
            h["shift"] = shift
        return h

    def gather_consumption(self, sheet, fields, entry):
        for row in read_sheet(self.wb, sheet):
            hno = heat_no_of(row)
            if not hno:
                continue
            day = HM.as_date(row.get("Date"))
            h = self.slot(hno, day, entry, HM.norm_head(row.get("Grade")),
                          HM.norm_head(row.get("Shift")))
            fmap = {**fields, **HM.LRF_BAND_FIELDS}
            apply_fields(row, fmap, h["day"] or day, h["data"], self.unmapped)
            collect_materials(row, entry, h["mats"])
            for head, key in HM.CASTER_LOSSES.items():
                v = HM.as_num(row.get(head))
                if v:
                    h["data"]["caster"][key] = v
            self.stats[f"rows:{sheet}"] += 1

    def gather_generic(self, sheet, fields):
        for row in read_sheet(self.wb, sheet):
            hno = heat_no_of(row)
            if not hno:
                continue
            day = HM.as_date(row.get("Date"))
            h = self.slot(hno, day)
            apply_fields(row, fields, h["day"] or day, h["data"], self.unmapped)
            collect_materials(row, h["entry"], h["mats"])
            self.stats[f"rows:{sheet}"] += 1

    def gather_delays(self, sheet, fields, stage_hint):
        """Furnace delay sheets carry BOTH cycle fields and the delay event."""
        for row in read_sheet(self.wb, sheet):
            hno = heat_no_of(row)
            if not hno:
                continue
            day = HM.as_date(row.get("Date"))
            h = self.slot(hno, day, shift=HM.norm_head(row.get("Shift")))
            apply_fields(row, fields, h["day"] or day, h["data"], self.unmapped)
            agency_raw = row.get("Agency")
            agency = HM.canon_agency(agency_raw)
            if not agency:
                continue
            self.agencies[agency] += 1
            if agency.strip().lower() in HM.NO_DELAY:
                self.stats["delays:no-delay rows skipped"] += 1
                continue
            code = HM.norm_head(row.get("Delay Code") or row.get("Delay code")
                                or row.get("Delay Reason"))
            desc = HM.norm_head(row.get("Delay Description")
                                or row.get("delay description")
                                or row.get("Description"))
            dur = HM.as_num(row.get("Delay Duration"))
            if dur is None or dur <= 0:
                # LRFVD/BC "Delay Duration" is a variance against standard time
                # and is routinely negative — that is not a delay event.
                self.stats["delays:non-positive skipped"] += 1
                continue
            st = HM.as_time(row.get("Delay Start Time") or row.get("Delay Start"))
            en = HM.as_time(row.get("Delay End Time") or row.get("Delay End"))
            stage = stage_hint if stage_hint != "entry" else h["entry"]
            h["delays"].append({"stage": stage, "agency": agency, "code": code,
                                "desc": desc, "min": dur,
                                "start": st, "end": en, "day": h["day"] or day})
            self.stats["delays:collected"] += 1

    def gather_all(self):
        self.gather_consumption("EAF CONSP", HM.EAF_FIELDS, "eaf")
        self.gather_consumption("ZPF CONSP", HM.ZPF_FIELDS, "zpf")
        self.gather_generic("LRFVD", HM.LRFVD_FIELDS)
        for s in ("BC2", "BC3"):
            for row in read_sheet(self.wb, s):
                hno = heat_no_of(row)
                if not hno:
                    continue
                day = HM.as_date(row.get("Date"))
                h = self.slot(hno, day, None, HM.norm_head(row.get("Grade")),
                              HM.norm_head(row.get("Shift")))
                apply_fields(row, HM.CASTER_FIELDS, h["day"] or day,
                             h["data"], self.unmapped)
                h["data"]["caster"]["ccm_no"] = "BC-2" if s == "BC2" else "BC-3"
                self.stats[f"rows:{s}"] += 1
        self.gather_delays("Delay EAF", HM.DELAY_EAF_FIELDS, "eaf")
        self.gather_delays("Delay ZPF", HM.DELAY_ZPF_FIELDS, "zpf")
        self.gather_delays("Delay LRF", HM.LRFVD_FIELDS, "lrf")
        self.gather_delays("Delay BC2", HM.CASTER_FIELDS, "caster")
        self.gather_delays("Delay BC3", HM.CASTER_FIELDS, "caster")

    # -- write ----------------------------------------------------------
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

    def write(self):
        existing = {h.heat_no for h in self.db.query(M.Heat.heat_no)}
        written = 0
        for hno, h in sorted(self.heats.items()):
            if hno in existing:
                self.stats["heats skipped (already present)"] += 1
                continue
            if not h["day"]:
                self.stats["heats skipped (no date)"] += 1
                continue
            moment = plant_time.resolve(datetime.combine(h["day"], datetime.min.time()))
            grade = self.resolve_grade(h["grade"])
            heat = M.Heat(heat_no=hno, grade_id=grade.id if grade else None,
                          status="locked", entry_stage=h["entry"],
                          is_imported=True,
                          plant_day=h["day"], shift=(h["shift"] or moment.shift),
                          shift_engineer=h["data"].get("lrf", {}).get("shift_incharge", ""),
                          created_by=self.user.id if self.user else None,
                          locked_at=datetime.combine(h["day"], datetime.min.time()))
            self.db.add(heat)
            self.db.flush()

            mats_by_stage = defaultdict(list)
            for stage, code, qty, point in h["mats"]:
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
            stages.discard("eaf" if h["entry"] == "zpf" else "___")
            # Machine identifiers normalised onto the codes the Gantt and the
            # route law use — the sheets write a bare 5 for the LRF and 2.1 for
            # the VD tank, which match no lane. Done before the records are
            # built so the stored data and the heat's route always agree.
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
                # imported without one is invisible on the plant's main board
                # however complete its data is. Anchor each record to the
                # process times the plant actually recorded.
                starts, ends = STAGE_SPAN.get(stage, ((), ()))
                started = _first_time(data, starts)
                ended = _first_time(data, ends)
                if data and not started:
                    started = ended or datetime.combine(h["day"], datetime.min.time())
                if started and ended and ended < started:
                    ended = started
                self.db.add(M.StageRecord(
                    heat_id=heat.id, stage=stage, data=data,
                    status="submitted" if data else "pending",
                    plant_day=h["day"], shift=(h["shift"] or moment.shift),
                    started_at=started,
                    submitted_at=(ended or started) if data else None))

            for d in h["delays"]:
                reason = self.resolve_reason(d["agency"], d["code"])
                start = dt_of(d["day"], d["start"]) or datetime.combine(
                    d["day"], datetime.min.time())
                end = dt_of(d["day"], d["end"])
                self.db.add(M.Delay(
                    heat_id=heat.id, stage=d["stage"], start_time=start,
                    end_time=end, duration_min=d["min"],
                    reason_id=reason.id if reason else None,
                    remarks=(d["desc"] or d["code"] or "")[:400]))
                self.stats["delays written"] += 1

            self.db.add(M.HeatEvent(
                heat_id=heat.id, heat_no=hno, kind="imported", at=heat.locked_at,
                plant_day=h["day"], shift=heat.shift, actor_name="History Import",
                detail=f"Backfilled from the plant's historical workbook — "
                       f"melted in the {h['entry'].upper()}. Not operator-entered; "
                       f"carries no approval signatures."))
            written += 1
            self.stats["heats imported"] += 1
            if written % 50 == 0:
                self.db.flush()
        self.advance_series()
        return written

    def advance_series(self):
        """Move each heat-number series past the history just loaded.

        Backfilling real heats consumes real numbers. If the series still
        points inside the imported range, the very next heat an operator
        creates collides with a historical one and the insert fails on the
        unique heat_no constraint. Each series is therefore advanced to one
        past the highest number now in use for its prefix.
        """
        for s in self.db.query(M.HeatSeries):
            used = [h.heat_no for h in self.db.query(M.Heat.heat_no)
                    .filter(M.Heat.heat_no.like(f"{s.prefix}%"))]
            top = 0
            for n in used:
                digits = n[len(s.prefix):]
                if digits.isdigit():
                    top = max(top, int(digits))
            if top >= s.next_number:
                self.stats[f"series {s.prefix}: advanced to {top + 1}"] += 1
                s.next_number = top + 1


def main():
    ap = argparse.ArgumentParser(description="Import IDOS.xlsx production history.")
    ap.add_argument("xlsx", nargs="?",
                    default=r"E:/Projects/Assets/IDOS.xlsx")
    ap.add_argument("--dry-run", action="store_true",
                    help="analyse and report; write nothing")
    a = ap.parse_args()

    Base.metadata.create_all(bind=engine)
    wb = openpyxl.load_workbook(a.xlsx, data_only=True)
    db = SessionLocal()
    try:
        imp = Importer(db, wb, a.dry_run)
        imp.gather_all()

        pref = Counter(h[0] for h in imp.heats)
        entry = Counter(v["entry"] for v in imp.heats.values())
        dated = sum(1 for v in imp.heats.values() if v["day"])
        days = [v["day"] for v in imp.heats.values() if v["day"]]
        print(f"\nHEATS FOUND: {len(imp.heats)}   prefixes={dict(pref)}   entry={dict(entry)}")
        print(f"  with a usable date: {dated}"
              + (f"   window {min(days)} .. {max(days)}" if days else ""))
        if days and (min(days) < HM.WINDOW[0] or max(days) > HM.WINDOW[1]):
            print(f"  !! dates fall outside the workbook's real window {HM.WINDOW} "
                  f"— check the day/month reading")
        for k in sorted(imp.stats):
            print(f"  {k}: {imp.stats[k]}")
        print(f"\nDELAY AGENCIES SEEN: {dict(imp.agencies.most_common())}")
        if imp.unmapped:
            print(f"\nUNMAPPED COLUMNS ({len(imp.unmapped)}) — add to history_map.py if wanted:")
            for h, n in imp.unmapped.most_common(40):
                print(f"   {n:>5}  {h}")
        else:
            print("\nUNMAPPED COLUMNS: none — every column has a home.")

        if a.dry_run:
            print("\nDRY RUN — nothing written.")
            db.rollback()
            return

        n = imp.write()
        if imp.unknown_mat:
            print(f"\n!! material codes not in the master: {dict(imp.unknown_mat)}")
        if imp.unknown_agency:
            print(f"!! agencies with no delay-master entry: {dict(imp.unknown_agency)}")
        db.add(M.AuditLog(
            user_name="History Import", category="admin", action="history_imported",
            detail=f"{n} heats backfilled from {Path(a.xlsx).name} "
                   f"({dict(pref)}). Historical records — no approval signatures."))
        db.commit()
        print(f"\nIMPORTED {n} heats.")
        print("Restart the server if the Gantt looks stale (SQLite WAL).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
