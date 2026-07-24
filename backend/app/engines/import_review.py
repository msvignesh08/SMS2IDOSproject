"""
SMS-2 IDOS — IMPORT CORRECTION & VALIDATION ENGINE
===================================================
Reads the plant's IDOS_Data workbook, finds what is missing or malformed, and
proposes a fix for each — with the reason it believes the fix is right.

THE CORRECTION APPROVAL LAW
    Nothing here writes production data. Every proposal waits for a human to
    accept it, whatever their role, and the raw cell value is kept beside the
    correction so an approval can be explained months later. A rejected
    proposal leaves the original exactly as it was.

WHY THE RULES ARE DETERMINISTIC
    This must work on a plant intranet with no internet, so the engine is
    rules over the plant's own structure, not a model call. The rules were
    written against the real workbook (~70,000 rows) and each one is grounded
    in something measured there, not assumed:

      forward-fill heat number   5,207 blank keys, and 100% of them sit
                                 directly under a filled one — they are
                                 continuation rows (a second delay on the same
                                 heat), not lost data.
      midnight-crossing duration ~19,400 negative durations. A delay that
                                 starts 23:50 and ends 00:10 computes as -1420
                                 minutes in the sheet; it is 20.
      1900-epoch time            1,595 cells where Excel rolled a time past
                                 midnight into 1900-01-01.
      formula error              45,395 #REF!/#VALUE! cells. These are NOT
                                 correctable — reported, never guessed.
      heat number format         three spellings in use: E251528, E25-2444 and
                                 25E1234. All name the same heat.

    Where the engine cannot be sure, it says so and proposes nothing. A wrong
    auto-correction that looks confident is worse than a blank.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

# ═══════════════════════════════════════════════════════════════════════════
# THE WORKBOOK'S SHAPE
#
# Column positions are 1-based and read from row 2, which is where this
# workbook puts its headers (row 1 is a spacer). Only the columns the plant
# actually uses are mapped; the sheets carry thousands of phantom columns
# after them.
# ═══════════════════════════════════════════════════════════════════════════

SHEETS = {
    "EAF Delay": {
        "stage": "eaf", "entry": "eaf", "kind": "delay", "header_row": 2,
        "cols": {"plant_day": 1, "shift": 2, "heat_no": 3,
                 "prev_tap": 4, "power_on": 5, "tap_time": 6,
                 "pon_to_tap": 7, "ttt": 8,
                 "delay_start": 9, "delay_end": 10, "delay_min": 11,
                 "agency": 12, "delay_code": 13, "delay_remarks": 14},
        "mandatory": ["plant_day", "shift", "heat_no"],
    },
    "EAF Consp": {
        "stage": "eaf", "entry": "eaf", "kind": "consumption", "header_row": 2,
        "cols": {"plant_day": 2, "heat_no": 3, "ladle_no": 4, "grade": 5,
                 "energy_kwh": 7, "o2_consumption": 11,
                 "hot_metal": 16, "dri_feed": 17, "hm_pct": 18, "dri_pct": 19,
                 "yield_pct": 20, "total_charge": 21, "tap_weight": 22,
                 "lime": 23, "dololime": 24, "tapping_o2": 27,
                 "tap_temp": 28, "hot_heel": 29, "lead_seq": 30,
                 "lrf_unit": 31, "vd_tank": 32},
        "mandatory": ["plant_day", "heat_no", "grade"],
    },
    "ZPF Delay": {
        # HEADER IS OFFSET BY ONE FROM THE DATA. Row 2 opens with a "Sl. No."
        # column that the data rows never fill, so the date the header puts in
        # column 2 actually arrives in column 1, and so on across the sheet.
        # Mapped by where the VALUES are, not by where the header says they
        # should be — reading the header positions literally sent every row's
        # heat number to the previous tap time and lost all 11,034 of them.
        "stage": "zpf", "entry": "zpf", "kind": "delay", "header_row": 2,
        "cols": {"plant_day": 1, "shift": 2, "heat_no": 3,
                 "prev_tap": 4, "zpf_start": 5, "zpf_end": 6,
                 "ttt": 9,
                 "delay_start": 10, "delay_end": 11, "delay_min": 12,
                 "agency": 13, "delay_code": 14, "delay_remarks": 15},
        "mandatory": ["plant_day", "shift", "heat_no"],
    },
    "ZPF Consp": {
        "stage": "zpf", "entry": "zpf", "kind": "consumption", "header_row": 2,
        "cols": {"plant_day": 2, "heat_no": 3, "ladle_no": 4, "grade": 5,
                 "lead_seq": 6, "o2_consumption": 7,
                 "hot_metal": 8, "dri_feed": 9, "hm_pct": 10, "dri_pct": 11,
                 "yield_pct": 12, "total_charge": 13, "tap_weight": 14},
        "mandatory": ["plant_day", "heat_no", "grade"],
    },
    "LRF Delay": {
        "stage": "lrf", "entry": None, "kind": "delay", "header_row": 2,
        "cols": {"plant_day": 1, "lrf_unit": 2, "heat_no": 3, "ladle_no": 4,
                 "liquidus": 5, "grade": 6, "grade_type": 7,
                 "eaf_tap_time": 8, "temp_initial": 10,
                 "lrf_start": 12, "lrf_end": 13, "process_time": 14},
        "mandatory": ["plant_day", "heat_no"],
    },
    "BC2": {
        "stage": "caster", "entry": None, "kind": "casting", "header_row": 2,
        "caster": "BC-2",
        "cols": {"plant_day": 1, "shift": 2, "section": 3, "grade_type": 5,
                 "grade": 6, "seq_no": 7, "heat_no": 8, "seq_len": 9,
                 "cast_start": 10, "prep_time": 11, "delay_min": 12,
                 "agency": 13, "delay_code": 14},
        "mandatory": ["plant_day", "shift", "heat_no"],
    },
    "BC3": {
        "stage": "caster", "entry": None, "kind": "casting", "header_row": 2,
        "caster": "BC-3",
        "cols": {"plant_day": 1, "shift": 2, "section": 3, "grade_type": 5,
                 "grade": 6, "seq_no": 7, "heat_no": 8, "seq_len": 9,
                 "cast_start": 10, "prep_time": 11, "delay_min": 12,
                 "agency": 13, "delay_code": 14},
        "mandatory": ["plant_day", "shift", "heat_no"],
    },
}

LABELS = {
    "plant_day": "Date", "shift": "Shift", "heat_no": "Heat No",
    "grade": "Grade", "agency": "Agency", "delay_code": "Delay Code",
    "delay_min": "Delay Duration", "delay_start": "Delay Start",
    "delay_end": "Delay End", "ttt": "Total Time (TTT)",
    "power_on": "Power On Time", "tap_time": "Tap Time",
    "tap_temp": "Tapping Temp", "tap_weight": "Tap Weight",
    "energy_kwh": "EAF Power", "o2_consumption": "Oxygen",
    "hot_metal": "Hot Metal", "dri_feed": "DRI Feed", "hm_pct": "HM %",
    "total_charge": "Total Charge Wt", "lrf_unit": "LRF Unit",
    "vd_tank": "VD / Tank No", "ladle_no": "Ladle No", "shift_code": "Shift",
}


def label_of(f: str) -> str:
    return LABELS.get(f, f.replace("_", " ").title())


# ═══════════════════════════════════════════════════════════════════════════
# VALUE NORMALISERS
# ═══════════════════════════════════════════════════════════════════════════

ERR_CELL = re.compile(r"^#(REF|VALUE|DIV/0|N/A|NAME|NULL|NUM)")


def is_error_cell(v) -> bool:
    return isinstance(v, str) and bool(ERR_CELL.match(v.strip()))


def blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def canon_heat(v) -> str | None:
    """One heat, one spelling.

    The workbook writes the same heat three ways — E251528, E25-2444 and
    25E1234 — because three departments type it three ways. They must collapse
    to one key or the same heat imports as three.
    """
    if blank(v) or is_error_cell(v):
        return None
    s = str(v).strip().upper().replace(" ", "")
    s = re.sub(r"[^A-Z0-9]", "", s)          # E25-2444 -> E252444
    m = re.fullmatch(r"([EF])(\d{5,7})[A-Z]?", s)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    m = re.fullmatch(r"(\d{2})([EF])(\d{4,6})", s)   # 25E1234 -> E251234
    if m:
        return f"{m.group(2)}{m.group(1)}{m.group(3)}"
    return None


def canon_day(v):
    """Date, from any of the four spellings this workbook uses."""
    if blank(v) or is_error_cell(v):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%d-%m-%Y", "%d.%m.%y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def canon_time(v):
    """A clock time, undoing Excel's 1900-epoch roll.

    When a time crosses midnight Excel stores it as 1900-01-01 00:47, so the
    date part is meaningless and only the clock face matters.
    """
    if blank(v) or is_error_cell(v):
        return None, False
    if isinstance(v, time):
        return v, False
    if isinstance(v, datetime):
        return v.time(), v.year == 1900
    s = str(v).strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h < 24 and mi < 60:
            return time(h, mi, int(m.group(3) or 0)), False
    return None, False


def canon_num(v):
    if blank(v) or is_error_cell(v):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except ValueError:
        return None


def tidy_num(x: float) -> float:
    """52.99999999999997 is 53. Excel's float noise, not a measurement."""
    r = round(x, 6)
    return float(round(r)) if abs(r - round(r)) < 1e-6 else r


# A single delay on one heat-stage longer than this is not credible, so a
# "correction" that produces one is rejected rather than proposed.
CREDIBLE_DELAY_MIN = 480.0

# This plant numbers heats with six digits after the prefix: E251528, F263581.
CANON_HEAT_DIGITS = 6


def minutes_between(t0: time, t1: time) -> float:
    """Elapsed minutes, treating an end before a start as crossing midnight.

    A delay logged 23:50 -> 00:10 is 20 minutes. The sheet computes it as
    -1420 because it subtracts clock faces without a date.
    """
    a = t0.hour * 60 + t0.minute + t0.second / 60
    b = t1.hour * 60 + t1.minute + t1.second / 60
    d = b - a
    return d if d >= 0 else d + 1440


# ═══════════════════════════════════════════════════════════════════════════
# THE ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class Issue(dict):
    pass


# Why each rule believes its correction is right. Held ONCE per rule rather
# than copied onto every finding: these paragraphs run ~300 characters and
# float_noise alone fires 47,613 times, so per-finding storage cost hundreds
# of megabytes on a 512 MB box for text that never varies.
BASIS: dict[str, str] = {}


def _issue(sheet, row_no, heat_no, field, severity, rule, message,
           raw="", proposed=None, confidence="medium", basis=""):
    if basis and rule not in BASIS:
        BASIS[rule] = basis
    return Issue(sheet=sheet, row_no=row_no, heat_no=heat_no or "",
                 field=field, label=label_of(field), severity=severity,
                 rule=rule, message=message,
                 raw_value="" if raw is None else str(raw)[:200],
                 proposed=None if proposed is None else str(proposed)[:200],
                 confidence=confidence)


def basis_of(rule: str) -> str:
    return BASIS.get(rule, "")


def analyse(wb, limit_rows: int | None = None) -> dict:
    """Parse the workbook, correct what can be corrected, report the rest.

    Returns staged rows plus the issue list. Writes nothing.
    """
    issues: list[Issue] = []
    rows: list[dict] = []
    sheet_stats: dict[str, dict] = {}

    for name, spec in SHEETS.items():
        if name not in wb.sheetnames:
            issues.append(_issue(name, 0, "", "", "warn", "sheet_missing",
                                 f"Sheet “{name}” is not in this workbook — "
                                 f"nothing imported from it.",
                                 basis="Expected by the IDOS_Data format."))
            continue
        ws = wb[name]
        cols = spec["cols"]
        hdr_row = spec["header_row"]
        last_heat = None
        last_day = None
        last_shift = None
        n_rows = n_fixed = 0

        # STREAM the rows, bounded to the widest mapped column.
        #
        # ws.cell(r, c) on a read-only worksheet re-walks the sheet for every
        # access, and EAF Consp is 16,371 columns wide with 7,904 rows — cell
        # access there does not finish in any time an operator would wait.
        # iter_rows reads each row once, and max_col stops it materialising
        # the thousands of phantom columns after the plant's real data.
        max_col = max(cols.values())
        for r, tup in enumerate(
                ws.iter_rows(min_row=hdr_row + 1, max_col=max_col,
                             values_only=True),
                start=hdr_row + 1):
            if limit_rows and n_rows >= limit_rows:
                break
            raw = {f: (tup[c - 1] if c - 1 < len(tup) else None)
                   for f, c in cols.items()}
            if all(blank(v) for v in raw.values()):
                continue
            n_rows += 1
            rec: dict = {"_sheet": name, "_row": r, "_stage": spec["stage"],
                         "_kind": spec["kind"], "_entry": spec.get("entry")}

            # ── heat number ──────────────────────────────────────────────
            hn = canon_heat(raw.get("heat_no"))
            if hn:
                if str(raw.get("heat_no") or "").strip().upper().replace(" ", "") != hn:
                    issues.append(_issue(
                        name, r, hn, "heat_no", "info", "heat_no_format",
                        "Heat number rewritten to the plant's single spelling.",
                        raw=raw.get("heat_no"), proposed=hn, confidence="high",
                        basis="This workbook writes the same heat as E251528, "
                              "E25-2444 and 25E1234. They must collapse to one "
                              "key or one heat imports as three."))
                    n_fixed += 1
                last_heat = hn
            elif blank(raw.get("heat_no")) and last_heat:
                # THE CONTINUATION ROW. Measured: every blank heat number in
                # this workbook sits directly under a filled one — a second
                # delay on the same heat, where the clerk did not retype it.
                issues.append(_issue(
                    name, r, last_heat, "heat_no", "warn", "continuation_row",
                    f"Blank heat number — this row continues {last_heat}.",
                    raw="", proposed=last_heat, confidence="high",
                    basis="A second delay logged against the heat above. "
                          "Measured across this workbook: 5,207 blank heat "
                          "numbers, and every one sits directly beneath a "
                          "filled row."))
                hn = last_heat
                n_fixed += 1
            elif is_error_cell(raw.get("heat_no")):
                issues.append(_issue(
                    name, r, "", "heat_no", "error", "formula_error",
                    "Heat number is a broken spreadsheet formula — this row "
                    "cannot be imported.",
                    raw=raw.get("heat_no"), proposed=None,
                    basis="A #REF!/#VALUE! cell holds no recoverable value. "
                          "Guessing the heat would attach real production data "
                          "to the wrong one."))
                continue
            else:
                issues.append(_issue(
                    name, r, "", "heat_no", "error", "missing_key",
                    "No heat number, and no row above to continue from.",
                    raw=raw.get("heat_no"), proposed=None,
                    basis="The heat number is the key every other value hangs "
                          "off. Without it the row has nowhere to go."))
                continue
            # A heat number of the wrong length is a typed digit, not a heat.
            # 51 rows in the real workbook read E2261255 — seven digits where
            # this plant uses six. Flagged, never auto-corrected: E2261255
            # could mean E226125 or E261255 and the engine cannot tell which.
            # The row is still imported, because its process data is real.
            if len(hn) - 1 != CANON_HEAT_DIGITS:
                issues.append(_issue(
                    name, r, hn, "heat_no", "warn", "heat_no_suspect",
                    f"Heat number carries {len(hn) - 1} digits; this plant "
                    f"uses {CANON_HEAT_DIGITS}.",
                    raw=raw.get("heat_no"), proposed=None,
                    basis="Almost certainly a mistyped digit. The row is kept "
                          "and its readings imported, but the number is left "
                          "exactly as written — guessing which digit is "
                          "spurious would attach real production data to a "
                          "heat that never existed. Fix it in the source "
                          "sheet. Numbers like this are also excluded from "
                          "advancing the heat-number series, so one typo "
                          "cannot push the plant's next heat into the "
                          "millions."))
            rec["heat_no"] = hn

            # ── plant day ────────────────────────────────────────────────
            d = canon_day(raw.get("plant_day"))
            if d:
                last_day = d
            elif last_day:
                issues.append(_issue(
                    name, r, hn, "plant_day", "warn", "continuation_row",
                    f"Blank date — continues the row above ({last_day}).",
                    raw="", proposed=last_day.isoformat(), confidence="high",
                    basis="Continuation rows repeat the heat, not the date."))
                d = last_day
                n_fixed += 1
            else:
                issues.append(_issue(
                    name, r, hn, "plant_day", "error", "missing_mandatory",
                    "No date, and none above to inherit.",
                    raw=raw.get("plant_day"), proposed=None,
                    basis="The Plant Date Engine files every heat by plant "
                          "day. Without it the row cannot be placed."))
            rec["plant_day"] = d.isoformat() if d else None

            # ── shift ────────────────────────────────────────────────────
            if "shift" in cols:
                sh = str(raw.get("shift") or "").strip().upper()[:1]
                if sh in ("A", "B", "C"):
                    last_shift = sh
                elif last_shift:
                    issues.append(_issue(
                        name, r, hn, "shift", "warn", "continuation_row",
                        f"Blank shift — continues the row above ({last_shift}).",
                        raw=raw.get("shift"), proposed=last_shift,
                        confidence="high",
                        basis="Same heat, same shift."))
                    sh = last_shift
                    n_fixed += 1
                else:
                    sh = None
                    issues.append(_issue(
                        name, r, hn, "shift", "warn", "missing_mandatory",
                        "No shift recorded.",
                        raw=raw.get("shift"), proposed=None,
                        basis="The plant day runs 22:00-22:00 across three "
                              "shifts; the engine will not guess which."))
                rec["shift"] = sh

            # ── times, undoing the 1900 roll ─────────────────────────────
            for f in ("power_on", "tap_time", "delay_start", "delay_end",
                      "zpf_start", "zpf_end", "lrf_start", "lrf_end",
                      "cast_start", "prev_tap", "eaf_tap_time"):
                if f not in cols:
                    continue
                t, rolled = canon_time(raw.get(f))
                if rolled:
                    issues.append(_issue(
                        name, r, hn, f, "info", "epoch_time",
                        f"Time stored as 1900-01-01 {t} — kept the clock face.",
                        raw=raw.get(f), proposed=t.strftime("%H:%M"),
                        confidence="high",
                        basis="Excel rolls a time past midnight onto its zero "
                              "date. Only the clock face is meaningful."))
                    n_fixed += 1
                rec[f] = t.strftime("%H:%M") if t else None

            # ── durations, undoing midnight-crossing negatives ───────────
            for f, (s_f, e_f) in (("delay_min", ("delay_start", "delay_end")),
                                  ("ttt", (None, None))):
                if f not in cols:
                    continue
                v = raw.get(f)
                if is_error_cell(v):
                    issues.append(_issue(
                        name, r, hn, f, "warn", "formula_error",
                        f"{label_of(f)} is a broken formula.",
                        raw=v, proposed=None,
                        basis="Not recoverable from the cell. Recompute it "
                              "from the times if they are present, or leave "
                              "it blank — a delay minute count is reported to "
                              "management and must not be invented."))
                    rec[f] = None
                    continue
                n = canon_num(v)
                if n is None:
                    rec[f] = None
                    continue
                if n < 0:
                    # NOT every negative is a midnight crossing, and treating
                    # them alike invents data. Measured across this workbook's
                    # 8,989 negative durations: 8,848 are under 60 minutes in
                    # magnitude and 126 are near a full day. A period that
                    # genuinely crosses 00:00 leaves a remainder close to
                    # -1440 (23:50->00:10 reads -1420); a -37 or -56 is an end
                    # time before its start on the SAME day, which is a typing
                    # error whose intent the engine cannot read.
                    t0 = rec.get(s_f) if s_f else None
                    t1 = rec.get(e_f) if e_f else None
                    fixed = None
                    if t0 and t1:
                        cand = tidy_num(minutes_between(
                            datetime.strptime(t0, "%H:%M").time(),
                            datetime.strptime(t1, "%H:%M").time()))
                        # Only believe it if the recomputed span is a credible
                        # delay. 1384 minutes is not a delay, it is a wrong
                        # answer stated confidently.
                        if cand <= CREDIBLE_DELAY_MIN:
                            fixed = cand
                    if fixed is not None:
                        issues.append(_issue(
                            name, r, hn, f, "warn", "midnight_crossing",
                            f"{label_of(f)} is negative ({tidy_num(n)}) — the "
                            f"period crosses midnight.",
                            raw=v, proposed=fixed, confidence="high",
                            basis=f"{t0} to {t1} spans 00:00. The sheet "
                                  f"subtracts clock faces with no date, so it "
                                  f"reports the negative remainder instead of "
                                  f"the {fixed} minutes actually elapsed."))
                        rec[f] = fixed
                        n_fixed += 1
                        continue
                    issues.append(_issue(
                        name, r, hn, f, "warn", "negative_duration",
                        f"{label_of(f)} is negative ({tidy_num(n)}) and cannot "
                        f"be recovered.",
                        raw=v, proposed=None,
                        basis=("End time is before the start on the same day — "
                               "a typing error whose intent cannot be read. "
                               if t0 and t1 else
                               "No start/end times on this row to recompute "
                               "from. ") +
                              "Delay minutes are reported to management, so "
                              "this is left blank rather than guessed."))
                    rec[f] = None
                    continue
                rec[f] = tidy_num(n)

            # ── everything else ──────────────────────────────────────────
            for f in cols:
                if f in rec or f in ("heat_no", "plant_day", "shift"):
                    continue
                v = raw.get(f)
                if is_error_cell(v):
                    issues.append(_issue(
                        name, r, hn, f, "warn", "formula_error",
                        f"{label_of(f)} is a broken spreadsheet formula.",
                        raw=v, proposed=None,
                        basis="Reported rather than guessed — a fabricated "
                              "process value is worse than a blank one."))
                    rec[f] = None
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    t = tidy_num(float(v))
                    if abs(float(v) - t) > 1e-12:
                        issues.append(_issue(
                            name, r, hn, f, "info", "float_noise",
                            f"{label_of(f)} rounded from {v} to {t}.",
                            raw=v, proposed=t, confidence="high",
                            basis="Excel float noise, not a measurement."))
                        n_fixed += 1
                    rec[f] = t
                elif not blank(v):
                    rec[f] = str(v).strip()

            # mandatory fields still empty after every rule has run
            for f in spec["mandatory"]:
                if rec.get(f) in (None, ""):
                    if not any(i["row_no"] == r and i["field"] == f
                               and i["sheet"] == name for i in issues):
                        issues.append(_issue(
                            name, r, hn, f, "error", "missing_mandatory",
                            f"{label_of(f)} is mandatory and empty.",
                            raw="", proposed=None,
                            basis="Required by this plant's entry rules."))
            rows.append(rec)

        sheet_stats[name] = {"rows": n_rows, "auto_corrected": n_fixed,
                             "stage": spec["stage"], "kind": spec["kind"]}

    heats = sorted({r["heat_no"] for r in rows if r.get("heat_no")})
    by_sev = {"error": 0, "warn": 0, "info": 0}
    for i in issues:
        by_sev[i["severity"]] = by_sev.get(i["severity"], 0) + 1
    correctable = sum(1 for i in issues if i["proposed"] is not None)

    return {"rows": rows, "issues": issues, "sheets": sheet_stats,
            "basis": dict(BASIS),
            "stats": {"rows": len(rows), "heats": len(heats),
                      "issues": len(issues), "by_severity": by_sev,
                      "correctable": correctable,
                      "not_correctable": len(issues) - correctable,
                      "eaf_heats": sum(1 for h in heats if h.startswith("E")),
                      "zpf_heats": sum(1 for h in heats if h.startswith("F"))}}
