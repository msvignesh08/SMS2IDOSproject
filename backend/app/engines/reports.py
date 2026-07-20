"""
SMS-2 IDOS — INTELLIGENT REPORTING & ANALYTICS CENTER engine (Section 9)
Report data assembly + Excel export (openpyxl). Every generated file is
archived immutably (Digital Time Machine, 11.14; reports as knowledge
assets, 9.30). Training heats are excluded from production reports.
"""
from __future__ import annotations
import os
from datetime import date, datetime
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
