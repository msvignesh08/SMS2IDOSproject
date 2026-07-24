"""
SMS-2 IDOS — INTELLIGENT REPORTING & ANALYTICS CENTER engine (Section 9)
Report data assembly + Excel export (openpyxl). Every generated file is
archived immutably (Digital Time Machine, 11.14; reports as knowledge
assets, 9.30). Training heats are excluded from production reports.
"""
from __future__ import annotations
import os
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from .. import models as M
from ..core import plant_time
from ..core.config import DATA_DIR
from .stage_config import STAGE_DEFS, required_stages

REPORT_DIR = os.path.join(DATA_DIR, "reports")
os.makedirs(REPORT_DIR, exist_ok=True)

HDR = Font(bold=True, color="FFFFFF", size=11)
FILL = PatternFill("solid", fgColor="1F3A5F")
THIN = Border(*[Side(style="thin", color="B8C2CC")] * 4)


def _prod_heats_query(db: Session):
    return db.query(M.Heat).filter(M.Heat.is_training == False,   # noqa: E712
                                   M.Heat.archived == False)      # noqa: E712


def daily_report_data(db: Session, plant_day: date) -> dict:
    start, end = plant_time.plant_day_window(plant_day)
    heats = (_prod_heats_query(db)
             .filter(M.Heat.plant_day == plant_day)
             .order_by(M.Heat.created_at).all())
    rows, per_shift = [], {"C": dict(heats=0, billets=0, tonnes=0.0),
                           "A": dict(heats=0, billets=0, tonnes=0.0),
                           "B": dict(heats=0, billets=0, tonnes=0.0)}
    tot_energy = tot_o2 = tot_billet_t = 0.0
    tot_billets = 0
    for h in heats:
        recs = {r.stage: (r.data or {}) for r in
                db.query(M.StageRecord).filter_by(heat_id=h.id)}
        eaf, cast = recs.get("eaf", {}), recs.get("caster", {})
        energy = float(eaf.get("energy_kwh") or 0)
        o2 = float(eaf.get("o2_consumption") or 0)
        billets = int(float(cast.get("billet_count") or 0))
        bwt = float(cast.get("billet_weight") or 0)
        tap = float(eaf.get("tap_weight") or 0)
        yield_pct = round(bwt / tap * 100, 1) if tap and bwt else None
        rows.append({"heat_no": h.heat_no, "grade": h.grade.code if h.grade else "",
                     "shift": h.shift, "route": h.route or "—", "status": h.status,
                     "tap_weight": tap or None, "energy_kwh": energy or None,
                     "o2": o2 or None, "billets": billets or None,
                     "billet_t": bwt or None, "yield_pct": yield_pct})
        tot_energy += energy; tot_o2 += o2
        tot_billets += billets; tot_billet_t += bwt
        if h.shift in per_shift:
            per_shift[h.shift]["heats"] += 1
            per_shift[h.shift]["billets"] += billets
            per_shift[h.shift]["tonnes"] += bwt
    delays = (db.query(M.Delay).join(M.Heat, M.Delay.heat_id == M.Heat.id)
              .filter(M.Heat.plant_day == plant_day, M.Heat.is_training == False,  # noqa
                      M.Delay.superseded == False).all())                          # noqa
    delay_rows = [{"heat_no": d.heat_id and db.get(M.Heat, d.heat_id).heat_no,
                   "stage": STAGE_DEFS[d.stage]["title"],
                   "category": d.reason.category if d.reason else "",
                   "reason": d.reason.reason if d.reason else "",
                   "duration_min": d.duration_min, "remarks": d.remarks}
                  for d in delays]
    return {"plant_day": plant_day.isoformat(),
            "window": [start.isoformat(sep=" "), end.isoformat(sep=" ")],
            "heats": rows, "per_shift": per_shift,
            "totals": {"heats": len(rows), "billets": tot_billets,
                       "billet_t": round(tot_billet_t, 1),
                       "energy_kwh": round(tot_energy, 0),
                       "o2_nm3": round(tot_o2, 0),
                       "delay_min": round(sum(d["duration_min"] or 0 for d in delay_rows), 0),
                       "delay_count": len(delay_rows)},
            "delays": delay_rows}


def heat_report_data(db: Session, heat: M.Heat) -> dict:
    stages = []
    from .lifecycle import heat_required_stages
    for st in heat_required_stages(db, heat):
        r = db.query(M.StageRecord).filter_by(heat_id=heat.id, stage=st).first()
        if not r:
            continue
        chem = [{"label": c.sample_label, "time": c.sample_time and c.sample_time.isoformat(sep=" "),
                 "values": c.values}
                for c in db.query(M.ChemistrySample)
                .filter_by(heat_id=heat.id, stage=st, superseded=False)]
        mats = [{"material": a.material.name, "qty": a.quantity, "unit": a.unit,
                 "time": a.addition_time and a.addition_time.isoformat(sep=" "),
                 "remarks": a.remarks}
                for a in db.query(M.MaterialAddition)
                .filter_by(heat_id=heat.id, stage=st, superseded=False)]
        dels = [{"start": d.start_time.isoformat(sep=" "),
                 "end": d.end_time and d.end_time.isoformat(sep=" "),
                 "min": d.duration_min,
                 "reason": f"{d.reason.category} — {d.reason.reason}" if d.reason else "",
                 "remarks": d.remarks}
                for d in db.query(M.Delay)
                .filter_by(heat_id=heat.id, stage=st, superseded=False)]
        stages.append({"stage": st, "title": STAGE_DEFS[st]["title"],
                       "status": r.status, "data": r.data or {},
                       "submitted_at": r.submitted_at and r.submitted_at.isoformat(sep=" "),
                       "chemistry": chem, "materials": mats, "delays": dels})
    approvals = [{"level": a.level, "role": a.role_name, "user": a.user_name,
                  "at": a.at.isoformat(sep=" "), "signature": a.signature,
                  "decision": a.decision}
                 for a in db.query(M.Approval).filter_by(heat_id=heat.id, scope="final")]
    events = [{"at": e.at.isoformat(sep=" "), "kind": e.kind, "stage": e.stage,
               "actor": e.actor_name, "detail": e.detail}
              for e in db.query(M.HeatEvent).filter_by(heat_id=heat.id)
              .order_by(M.HeatEvent.at)]
    return {"heat_no": heat.heat_no, "grade": heat.grade.code if heat.grade else "",
            "plant_day": heat.plant_day.isoformat(), "shift": heat.shift,
            "shift_engineer": heat.shift_engineer, "route": heat.route,
            "status": heat.status, "is_training": heat.is_training,
            "created_at": heat.created_at.isoformat(sep=" "),
            "locked_at": heat.locked_at and heat.locked_at.isoformat(sep=" "),
            "stages": stages, "approvals": approvals, "timeline": events}


def _sheet_header(ws, title, subtitle):
    ws["A1"] = "SMS-2 IDOS — Steel Melting Shop"; ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = title; ws["A2"].font = Font(bold=True, size=12)
    ws["A3"] = subtitle
    ws["A4"] = f"Generated {datetime.now():%Y-%m-%d %H:%M} · People First. Production Always. Knowledge Forever."
    ws["A4"].font = Font(italic=True, size=9)


def _table(ws, row0, headers, rows):
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=row0, column=c, value=h)
        cell.font = HDR; cell.fill = FILL; cell.border = THIN
        cell.alignment = Alignment(horizontal="center")
    for r, data in enumerate(rows, row0 + 1):
        for c, v in enumerate(data, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = THIN
    for c in range(1, len(headers) + 1):
        ws.column_dimensions[chr(64 + c) if c <= 26 else "A"].width = 16
    return row0 + len(rows) + 2


def export_daily_xlsx(db: Session, plant_day: date, user_name: str) -> str:
    d = daily_report_data(db, plant_day)
    wb = Workbook(); ws = wb.active; ws.title = "Daily Production"
    _sheet_header(ws, "DAILY PRODUCTION REPORT",
                  f"Plant Day {d['plant_day']}  (22:00 → 22:00)")
    r = _table(ws, 6,
               ["Heat No", "Grade", "Shift", "Route", "Status", "Tap Wt (t)",
                "Energy (kWh)", "O₂ (Nm³)", "Billets", "Billet Wt (t)", "Yield %"],
               [[h["heat_no"], h["grade"], h["shift"], h["route"], h["status"],
                 h["tap_weight"], h["energy_kwh"], h["o2"], h["billets"],
                 h["billet_t"], h["yield_pct"]] for h in d["heats"]])
    ws.cell(row=r, column=1, value="SHIFT SUMMARY").font = Font(bold=True)
    r = _table(ws, r + 1, ["Shift", "Heats", "Billets", "Billet Wt (t)"],
               [[s, v["heats"], v["billets"], round(v["tonnes"], 1)]
                for s, v in d["per_shift"].items()])
    t = d["totals"]
    ws.cell(row=r, column=1, value="PLANT DAY TOTALS").font = Font(bold=True)
    _table(ws, r + 1, ["Heats", "Billets", "Billet Wt (t)", "Energy (kWh)",
                       "O₂ (Nm³)", "Delays", "Delay (min)"],
           [[t["heats"], t["billets"], t["billet_t"], t["energy_kwh"],
             t["o2_nm3"], t["delay_count"], t["delay_min"]]])
    ws2 = wb.create_sheet("Delays")
    _sheet_header(ws2, "DELAY REPORT", f"Plant Day {d['plant_day']}")
    _table(ws2, 6, ["Heat No", "Stage", "Category", "Reason", "Duration (min)", "Remarks"],
           [[x["heat_no"], x["stage"], x["category"], x["reason"],
             x["duration_min"], x["remarks"]] for x in d["delays"]])
    fname = f"daily_{d['plant_day']}_{datetime.now():%H%M%S}.xlsx"
    path = os.path.join(REPORT_DIR, fname)
    wb.save(path)
    from .lifecycle import notify as _notify
    _notify(db, f"Report generated — daily {d['plant_day']}",
            f"By {user_name}. Archived permanently in the Report Library.",
            kind="report")
    db.add(M.ReportArchive(kind="daily", ref=d["plant_day"], fmt="xlsx",
                           filename=fname, generated_by=user_name))
    return path


def export_heat_xlsx(db: Session, heat: M.Heat, user_name: str) -> str:
    d = heat_report_data(db, heat)
    wb = Workbook(); ws = wb.active; ws.title = "Heat Summary"
    _sheet_header(ws, f"HEAT REPORT — {d['heat_no']}",
                  f"Grade {d['grade']} · Plant Day {d['plant_day']} · Shift {d['shift']} · Route {d['route'] or '—'}")
    r = _table(ws, 6, ["Field", "Value"], [
        ["Heat Number", d["heat_no"]], ["Steel Grade", d["grade"]],
        ["Plant Day", d["plant_day"]], ["Shift", d["shift"]],
        ["Shift Engineer", d["shift_engineer"]], ["Route", d["route"] or "—"],
        ["Status", d["status"].upper()],
        ["Created", d["created_at"]], ["Locked", d["locked_at"] or "—"],
        ["Training Heat", "YES" if d["is_training"] else "No"]])
    for s in d["stages"]:
        ws.cell(row=r, column=1, value=s["title"].upper()).font = Font(bold=True, size=12)
        r += 1
        rows = [[k, v] for k, v in s["data"].items() if v not in (None, "", [])]
        if rows:
            r = _table(ws, r, ["Field", "Value"], rows)
        if s["chemistry"]:
            els = sorted({e for c in s["chemistry"] for e in c["values"]})
            r = _table(ws, r, ["Sample", "Time"] + els,
                       [[c["label"], c["time"]] + [c["values"].get(e) for e in els]
                        for c in s["chemistry"]])
        if s["materials"]:
            r = _table(ws, r, ["Material", "Qty", "Unit", "Time", "Remarks"],
                       [[m["material"], m["qty"], m["unit"], m["time"], m["remarks"]]
                        for m in s["materials"]])
        if s["delays"]:
            r = _table(ws, r, ["Delay Start", "End", "Min", "Reason", "Remarks"],
                       [[x["start"], x["end"], x["min"], x["reason"], x["remarks"]]
                        for x in s["delays"]])
    ws2 = wb.create_sheet("Timeline & Approvals")
    _sheet_header(ws2, "PRODUCTION TIMELINE — Digital DNA", d["heat_no"])
    r = _table(ws2, 6, ["Time", "Event", "Stage", "Actor", "Detail"],
               [[e["at"], e["kind"], e["stage"] or "", e["actor"], e["detail"]]
                for e in d["timeline"]])
    ws2.cell(row=r, column=1, value="DIGITAL APPROVALS").font = Font(bold=True)
    _table(ws2, r + 1, ["Level", "Role", "User", "Time", "Signature", "Decision"],
           [[a["level"], a["role"], a["user"], a["at"], a["signature"], a["decision"]]
            for a in d["approvals"]])
    fname = f"heat_{d['heat_no']}_{datetime.now():%H%M%S}.xlsx"
    path = os.path.join(REPORT_DIR, fname)
    wb.save(path)
    from .lifecycle import notify as _notify
    _notify(db, f"Report generated — heat {d['heat_no']}",
            f"By {user_name}. Archived permanently in the Report Library.",
            kind="report", heat_no=d["heat_no"])
    db.add(M.ReportArchive(kind="heat", ref=d["heat_no"], fmt="xlsx",
                           filename=fname, generated_by=user_name))
    return path


# ═══════════════════════════════════════════════════════════════════════════
#  DATE-RANGE EXPORTS
#
#  The single-day report answers "how did today go". These answer "how did
#  the month go" — the question asked at a review meeting, and the one that
#  previously meant exporting thirty files and pasting them together.
# ═══════════════════════════════════════════════════════════════════════════

MAX_RANGE_DAYS = 366


def _range_days(d0: date, d1: date) -> list:
    if d1 < d0:
        d0, d1 = d1, d0            # accept the dates either way round
    span = (d1 - d0).days + 1
    if span > MAX_RANGE_DAYS:
        raise ValueError(f"Range is {span} days. Export at most "
                         f"{MAX_RANGE_DAYS} days at a time.")
    return [d0 + timedelta(days=i) for i in range(span)]


def range_report_data(db: Session, d0: date, d1: date) -> dict:
    """Every plant day in the range, plus the totals across it."""
    days = _range_days(d0, d1)
    per_day, heats, delays = [], [], []
    tot = {"heats": 0, "billets": 0, "billet_t": 0.0, "energy_kwh": 0.0,
           "o2_nm3": 0.0, "delay_count": 0, "delay_min": 0.0}
    per_shift = {}
    per_grade = {}
    for day in days:
        d = daily_report_data(db, day)
        t = d["totals"]
        per_day.append({"plant_day": d["plant_day"], **t})
        for k in tot:
            tot[k] += t.get(k, 0) or 0
        for h in d["heats"]:
            heats.append({"plant_day": d["plant_day"], **h})
            g = per_grade.setdefault(h["grade"] or "-", {"heats": 0, "billet_t": 0.0})
            g["heats"] += 1
            g["billet_t"] += float(h.get("billet_t") or 0)
        for s, v in (d["per_shift"] or {}).items():
            acc = per_shift.setdefault(s, {"heats": 0, "billets": 0, "tonnes": 0.0})
            acc["heats"] += v["heats"]
            acc["billets"] += v["billets"]
            acc["tonnes"] += v["tonnes"]
        for x in d["delays"]:
            delays.append({"plant_day": d["plant_day"], **x})
    for k in ("billet_t", "energy_kwh", "o2_nm3", "delay_min"):
        tot[k] = round(tot[k], 1)
    return {"from": days[0].isoformat(), "to": days[-1].isoformat(),
            "days": len(days), "per_day": per_day, "heats": heats,
            "delays": delays, "totals": tot,
            "per_shift": per_shift, "per_grade": per_grade}


def export_range_xlsx(db: Session, d0: date, d1: date, user_name: str) -> str:
    d = range_report_data(db, d0, d1)
    wb = Workbook()

    ws = wb.active
    ws.title = "Summary"
    _sheet_header(ws, "PRODUCTION REPORT",
                  f"Plant days {d['from']} to {d['to']}  "
                  f"({d['days']} days, 22:00 to 22:00)")
    t = d["totals"]
    r = _table(ws, 6, ["Days", "Heats", "Billets", "Billet Wt (t)", "Energy (kWh)",
                       "O2 (Nm3)", "Delays", "Delay (min)"],
               [[d["days"], t["heats"], t["billets"], t["billet_t"],
                 t["energy_kwh"], t["o2_nm3"], t["delay_count"], t["delay_min"]]])
    ws.cell(row=r, column=1, value="BY SHIFT").font = Font(bold=True)
    r = _table(ws, r + 1, ["Shift", "Heats", "Billets", "Billet Wt (t)"],
               [[s, v["heats"], v["billets"], round(v["tonnes"], 1)]
                for s, v in sorted(per_shift_items(d))])
    ws.cell(row=r, column=1, value="BY GRADE").font = Font(bold=True)
    _table(ws, r + 1, ["Grade", "Heats", "Billet Wt (t)"],
           [[g, v["heats"], round(v["billet_t"], 1)]
            for g, v in sorted(d["per_grade"].items(), key=lambda x: -x[1]["heats"])])

    ws2 = wb.create_sheet("Per Day")
    _sheet_header(ws2, "DAY BY DAY", f"{d['from']} to {d['to']}")
    _table(ws2, 6, ["Plant Day", "Heats", "Billets", "Billet Wt (t)",
                    "Energy (kWh)", "O2 (Nm3)", "Delays", "Delay (min)"],
           [[x["plant_day"], x["heats"], x["billets"], x["billet_t"],
             x["energy_kwh"], x["o2_nm3"], x["delay_count"], x["delay_min"]]
            for x in d["per_day"]])

    ws3 = wb.create_sheet("Heats")
    _sheet_header(ws3, "EVERY HEAT", f"{d['from']} to {d['to']}")
    _table(ws3, 6, ["Plant Day", "Heat No", "Grade", "Shift", "Route", "Status",
                    "Tap Wt (t)", "Energy (kWh)", "O2 (Nm3)", "Billets",
                    "Billet Wt (t)", "Yield %"],
           [[h["plant_day"], h["heat_no"], h["grade"], h["shift"], h["route"],
             h["status"], h["tap_weight"], h["energy_kwh"], h["o2"],
             h["billets"], h["billet_t"], h["yield_pct"]] for h in d["heats"]])

    ws4 = wb.create_sheet("Delays")
    _sheet_header(ws4, "DELAY REPORT", f"{d['from']} to {d['to']}")
    _table(ws4, 6, ["Plant Day", "Heat No", "Stage", "Category", "Reason",
                    "Duration (min)", "Remarks"],
           [[x["plant_day"], x["heat_no"], x["stage"], x["category"],
             x["reason"], x["duration_min"], x["remarks"]] for x in d["delays"]])

    fname = f"production_{d['from']}_to_{d['to']}_{datetime.now():%H%M%S}.xlsx"
    path = os.path.join(REPORT_DIR, fname)
    wb.save(path)
    from .lifecycle import notify as _notify
    _notify(db, f"Report generated - production {d['from']} to {d['to']}",
            f"By {user_name}. {t['heats']} heats. Archived in the Report Library.",
            kind="report")
    db.add(M.ReportArchive(kind="range", ref=f"{d['from']}..{d['to']}", fmt="xlsx",
                           filename=fname, generated_by=user_name))
    return path


def per_shift_items(d):
    return d["per_shift"].items()


def export_audit_xlsx(db: Session, d0: date, d1: date, user_name: str,
                      category: str = "", heat_no: str = "") -> str:
    """The audit trail over a date range.

    THE AUDIT IS APPEND-ONLY AND SO IS ITS EXPORT: this reads, it never drops
    an entry to make the record look tidier.

    The bounds are resolved through the Plant Date Engine, so a range means
    PLANT days - 22:00 to 22:00 - not calendar midnights. Exporting a plant
    day and getting a calendar day would silently move every night-shift
    entry into the wrong report, which is exactly the kind of quiet error an
    audit trail exists to prevent.
    """
    days = _range_days(d0, d1)
    w0 = plant_time.plant_day_window(days[0])[0]
    w1 = plant_time.plant_day_window(days[-1])[1]
    q = (db.query(M.AuditLog)
         .filter(M.AuditLog.at >= w0, M.AuditLog.at <= w1)
         .order_by(M.AuditLog.at.asc()))
    if category:
        q = q.filter(M.AuditLog.category == category)
    if heat_no:
        q = q.filter(M.AuditLog.heat_no == heat_no.strip().upper())
    rows = q.all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Audit Trail"
    scope = f"Plant days {days[0]} to {days[-1]}"
    if category:
        scope += f" - category {category}"
    if heat_no:
        scope += f" - heat {heat_no.upper()}"
    _sheet_header(ws, "IMMUTABLE AUDIT TRAIL", scope)
    _table(ws, 6, ["Timestamp", "User", "Category", "Action", "Heat No", "Detail"],
           [[a.at.strftime("%Y-%m-%d %H:%M:%S"), a.user_name or "-",
             a.category, a.action, a.heat_no or "-", (a.detail or "")[:500]]
            for a in rows])

    ws2 = wb.create_sheet("Summary")
    _sheet_header(ws2, "ACTIVITY SUMMARY", scope)
    by_cat = {}
    by_user = {}
    for a in rows:
        by_cat[a.category or "-"] = by_cat.get(a.category or "-", 0) + 1
        by_user[a.user_name or "-"] = by_user.get(a.user_name or "-", 0) + 1
    r = _table(ws2, 6, ["Category", "Entries"],
               [[k, v] for k, v in sorted(by_cat.items(), key=lambda x: -x[1])])
    ws2.cell(row=r, column=1, value="BY USER").font = Font(bold=True)
    _table(ws2, r + 1, ["User", "Entries"],
           [[k, v] for k, v in sorted(by_user.items(), key=lambda x: -x[1])])

    fname = f"audit_{days[0]}_to_{days[-1]}_{datetime.now():%H%M%S}.xlsx"
    path = os.path.join(REPORT_DIR, fname)
    wb.save(path)
    db.add(M.ReportArchive(kind="audit", ref=f"{days[0]}..{days[-1]}", fmt="xlsx",
                           filename=fname, generated_by=user_name))
    return path
