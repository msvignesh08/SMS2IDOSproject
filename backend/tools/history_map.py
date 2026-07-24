"""
SMS-2 IDOS — IDOS.xlsx → application mapping tables
====================================================
Everything that describes the plant's historical workbook lives here, so the
importer (import_history.py) stays readable and a spreadsheet revision is a
data edit rather than a code change.

Three things the workbook does that the reader must respect:

1. HEADERS SPAN SEVERAL ROWS. "P. on" + "to Tap" + "Time" is one column, and
   the continuation rows sit exactly where data would be. Reading from the
   wrong row silently imports "to Tap" as a heat record — hence DATA_ROW.

2. THE SAME HEAT IS SPELLED DIFFERENTLY PER SHEET — F26-3581 in ZPF CONSP and
   LRFVD, F263581 in Delay ZPF and BC2. norm_heat() collapses both.

3. `EAF CONSP` CARRIES 127 PHANTOM COPIES of its header block out to column
   16371 (a copy-paste artefact). Only the first block holds data, so every
   sheet declares the last real column.
"""
import re
from datetime import date, datetime, time

# sheet -> (header_row, first_data_row, last_real_column)
SHEETS = {
    "EAF CONSP": (2, 3, 115),
    "ZPF CONSP": (2, 3, 106),
    "LRFVD":     (3, 4, 30),
    "BC2":       (3, 4, 27),
    "BC3":       (3, 4, 28),
    "Delay EAF": (1, 4, 16),
    "Delay ZPF": (1, 4, 20),
    "Delay LRF": (1, 3, 30),
    "Delay BC2": (1, 3, 27),
    "Delay BC3": (1, 3, 28),
}

# The window the workbook actually covers — used to catch date misreads.
WINDOW = (date(2026, 6, 25), date(2026, 7, 25))


def norm_heat(v) -> str:
    """F26-3581 / F263581 / ' e263126 ' -> F263581 / E263126."""
    return re.sub(r"[^A-Z0-9]", "", str(v).strip().upper()) if v not in (None, "") else ""


def norm_head(v) -> str:
    return re.sub(r"\s+", " ", str(v).strip()) if v not in (None, "") else ""


def _in_window(d):
    return d is not None and WINDOW[0] <= d <= WINDOW[1]


def repair_date(d):
    """Undo Excel's mangling of the caster sheets' dates.

    BC2/BC3 hold dates like 2026-12-07 and 2027-01-07 while every other sheet
    sits inside 30 Jun – 20 Jul 2026. The pattern is day/month transposed —
    "7 Dec" is really 12 July — and on the roll-over rows the year moved too.

    Rather than trust a guess, each candidate repair is only accepted if it
    lands inside the workbook's real window; anything that cannot be resolved
    is returned untouched and reported by --dry-run.
    """
    if d is None or _in_window(d):
        return d
    y = WINDOW[0].year
    for cand in (
        d.replace(year=y),                                  # year rolled
        date(d.year, d.day, d.month) if d.day <= 12 else None,      # d/m swapped
        date(y, d.day, d.month) if d.day <= 12 else None,           # both
    ):
        try:
            if _in_window(cand):
                return cand
        except (ValueError, TypeError):
            continue
    return d


def as_date(v):
    """Sheets store dates as real datetimes OR as dd/mm/yyyy text (BC2/BC3).
    Text is read day-first, which is what the plant writes."""
    d = None
    if isinstance(v, datetime):
        d = v.date()
    elif isinstance(v, date):
        d = v
    else:
        s = str(v or "").strip()
        if not s:
            return None
        for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                pass
    return repair_date(d)


def as_time(v):
    """Clock times arrive as datetime.time, datetime, or 'HH:MM(:SS)' text."""
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    s = str(v or "").strip()
    if not s:
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    return None


def as_num(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip().replace(",", ""))
    except ValueError:
        return None


# ── FIELD MAPS ────────────────────────────────────────────────────────────
# {sheet header : (stage, field_key, kind)}   kind: num|text|time|date
# A header absent from these maps is reported by --dry-run as unmapped.

EAF_FIELDS = {
    "Ladle NO.":                ("eaf", "ladle_no", "text"),
    "F/C":                      ("eaf", "furnace_campaign", "text"),
    "EAF Power":                ("eaf", "energy_kwh", "num"),
    "EAF Electrode Addition":   ("eaf", "electrode_addition", "text"),
    "Electrode Supplier Name":  ("eaf", "electrode_supplier", "text"),
    "EAF Electrode Break":      ("eaf", "electrode_break", "text"),
    "O2 (NM3)":                 ("eaf", "o2_consumption", "num"),
    "Hot Metal":                ("eaf", "hm_weight", "num"),
    "DRI Feed":                 ("eaf", "dri_feed", "num"),
    "Yield %":                  ("eaf", "yield_pct", "num"),
    "Tap Wt.":                  ("eaf", "tap_weight", "num"),
    "Mix.Gas":                  ("eaf", "mix_gas", "num"),
    "Tapping Oxygen":           ("eaf", "tapping_oxygen", "num"),
    "Tapping Temp.":            ("eaf", "tap_temp", "num"),
    "Hot Heel":                 ("eaf", "hot_heel", "num"),
    "LEAD SEQ":                 ("eaf", "lead_seq", "text"),
}

ZPF_FIELDS = {
    "Ladle No.":            ("zpf", "ladle_no", "text"),
    "Lead Seq.":            ("zpf", "lead_seq", "text"),
    "O2 (NM3)":             ("zpf", "o2_total", "num"),
    "Hot Metal":            ("zpf", "hm_weight", "num"),
    "DRI Feed":             ("zpf", "dri_feed", "num"),
    "Yield %":              ("zpf", "yield_pct", "num"),
    "Total LM Wt.":         ("zpf", "total_lm_wt", "num"),
    "Lpg Gas":              ("zpf", "lpg_gas", "num"),
    "Tapping Oxygen":       ("zpf", "tapping_oxygen", "num"),
    "Tapping Temp.":        ("zpf", "tap_temp", "num"),
    "Hot Heel":             ("zpf", "hot_heel", "num"),
    "ZPF argon":            ("zpf", "zpf_argon", "num"),
    "Compressed air":       ("zpf", "compressed_air", "num"),
}

# The "LRF" band that trails BOTH consumption sheets.
LRF_BAND_FIELDS = {
    "LRF":                  ("lrf", "lrf_unit", "text"),
    "VD/Tank No":           ("vd", "vd_tank", "text"),
    "VD":                   ("vd", "vd_tank", "text"),
    "LF Power":             ("lrf", "lf_power", "num"),
    "Aux. Power":           ("lrf", "aux_power", "num"),
    "Argon(LRF)":           ("lrf", "argon_lrf", "num"),
    "Argon(VD)":            ("vd", "argon_vd", "num"),
    "Nitrogen VD":          ("vd", "nitrogen_vd", "num"),
    "LF Electrode Addition": ("lrf", "lf_electrode_addition", "num"),
    "CCM":                  ("caster", "ccm_no", "text"),
    "LM wt.":               ("caster", "lm_weight", "num"),
    "Total Blt. Prod.":     ("caster", "billet_count", "num"),
}

# LRFVD / Delay LRF — the shared refining sheet (both divisions).
LRFVD_FIELDS = {
    "LRF5":                         ("lrf", "lrf_unit", "text"),
    "Ladle No":                     ("lrf", "ladle_no", "text"),
    "Liquidus":                     ("lrf", "liquidus", "num"),
    "Grade type":                   ("lrf", "grade_type", "text"),
    "EAF Tap Time":                 ("lrf", "eaf_tap_time", "time"),
    "Free Board":                   ("lrf", "free_board", "num"),
    "Free Board (mm)":              ("lrf", "free_board", "num"),
    "LRF Opening Temp.(°C)":        ("lrf", "temp_initial", "num"),
    "Opening %P":                   ("lrf", "opening_p", "num"),
    "LRF Power On Time":            ("lrf", "lrf_start", "time"),
    "LRF Lifting Time":             ("lrf", "lrf_end", "time"),
    "LRF Lifting Temp(°C)":         ("lrf", "temp_final", "num"),
    "Std process time":             ("lrf", "std_process_time", "num"),
    "LRF Engg":                     ("lrf", "lrf_engineer", "text"),
    "SHIFT Incharge":               ("lrf", "shift_incharge", "text"),
    "Remarks":                      ("lrf", "furnace_remarks", "text"),
    "VD Opening Temp.(°C)":         ("vd", "temp_initial", "num"),
    "Before Hydris Reading (PPM)":  ("vd", "hydris_before", "num"),
    "VD in":                        ("vd", "vd_start", "time"),
    "VD Out":                       ("vd", "vd_end", "time"),
    "VD Lifting Temp.(°C)":         ("vd", "temp_final", "num"),
    "VD Total STEAM consumption22": ("vd", "vd_steam", "num"),
    "After Hydris Reading (PPM)2":  ("vd", "hydris_after", "num"),
    "VD Engg":                      ("vd", "vd_engineer", "text"),
}

# BC2 / BC3 / Delay BC2 / Delay BC3 — the casters.
CASTER_FIELDS = {
    "Section(MM)":              ("caster", "billet_size", "section"),
    "Grade Type":               ("caster", "grade_type", "text"),
    "Seq.No.":                  ("caster", "seq_no", "text"),
    "Seq. Length":              ("caster", "seq_length", "text"),
    "Laddle on turret time":    ("caster", "ladle_on_turret", "time"),
    "Machine Preparation Time": ("caster", "machine_prep_time", "num"),
    "LM":                       ("caster", "lm_weight", "num"),
    "Cast Start":               ("caster", "cast_start", "time"),
    "Cast End":                 ("caster", "cast_end", "time"),
    "Targeted Casting Time":    ("caster", "targeted_cast_time", "num"),
    "Targeted Casting Speed":   ("caster", "targeted_speed", "num"),
    "Average Casting Speed":    ("caster", "cast_speed", "num"),
    "Superheat 1(in °C)":       ("caster", "superheat_1", "num"),
    "Superheat 2(in °C)":       ("caster", "superheat_2", "num"),
    "Superheat 2(in °C)2":      ("caster", "superheat_3", "num"),
    "Superheat 3(in °C)":       ("caster", "superheat_3", "num"),
    "Delay":                    ("caster", "cast_delay_reason", "text"),
    "Description":              ("caster", "cast_remarks", "text"),
}

# Delay EAF / Delay ZPF — furnace cycle columns (the delay event itself is
# handled separately by the importer).
DELAY_EAF_FIELDS = {
    "Pre Heat Tap Time":         ("eaf", "pre_heat_tap_time", "time"),
    "Power on Time":             ("eaf", "power_on", "time"),
    "Tap Time":                  ("eaf", "tap_end", "time"),
    "P. on":                     ("eaf", "pon_to_tap", "num"),
    "Total Time (TTT)":          ("eaf", "ttt", "num"),
    "EBT Free Opening":          ("eaf", "ebt_free_opening", "text"),
    "EBT FILLING MASS SUPPLIER": ("eaf", "ebt_filling_supplier", "text"),
}

DELAY_ZPF_FIELDS = {
    "Pre Heat Tap Time":         ("zpf", "pre_heat_tap_time", "time"),
    "Blow on Time":              ("zpf", "blow_on_time", "time"),
    "Tap Time":                  ("zpf", "tap_time", "time"),
    "Tap to B. on Time":         ("zpf", "tap_to_blow_on", "num"),
    "B.on":                      ("zpf", "bon_to_tap", "num"),
    "Total Time (TTT)":          ("zpf", "ttt", "num"),
    "Process Delay":             ("zpf", "process_delay", "num"),
    "EBT Free Opening":          ("zpf", "ebt_free_opening", "text"),
    "EBT FILLING MASS SUPPLIER": ("zpf", "ebt_filling_supplier", "text"),
}

# ── MATERIAL COLUMNS ──────────────────────────────────────────────────────
# {header : (material_code, stage_hint, addition_point)}
#   stage_hint "entry"  -> the furnace that melted the heat (eaf or zpf)
#   stage_hint "lrf"    -> the ladle furnace
# A "Tapping X" column is the same material as "X" but goes in at the furnace
# during tapping, so it lands on the entry stage with point="tapping".
MATERIALS = {
    # charge (metallic) — always at the entry furnace
    "Bundle":            ("BUNDLE", "entry", "charge"),
    "Pig Iron":          ("PIG", "entry", "charge"),
    "Skull":             ("SKL", "entry", "charge"),
    "Internal Return":   ("IRET", "entry", "charge"),
    "DRI Feed":          ("DRI", "entry", "charge"),
    # furnace fluxes
    "EAF Lime":          ("LIME", "entry", "process"),
    "EAF Dololime":      ("DOLO", "entry", "process"),
    "CPC EAF":           ("CARB", "entry", "process"),
    "ZPF Lime":          ("LIME", "entry", "process"),
    "ZPF Dolo- lime":    ("DOLO", "entry", "process"),
    "CPC ZPF":           ("CARB", "entry", "process"),
    # added into the ladle DURING TAPPING, at the furnace
    "Tapping Lime":              ("LIME", "entry", "tapping"),
    "Tapping Si-Mn":             ("SIMN", "entry", "tapping"),
    "Tapping Fe-Mn HC":          ("FEMNHC", "entry", "tapping"),
    "Tapping Fe-Mn MC":          ("FEMNMC", "entry", "tapping"),
    "Tapping  Fe-Mn MC":         ("FEMNMC", "entry", "tapping"),
    "Tapping Fe-Mn LC":          ("FEMNLC", "entry", "tapping"),
    "Tapping FeCr Lc":           ("FECRLC", "entry", "tapping"),
    "Tapping Fe-Cr LC":          ("FECRLC", "entry", "tapping"),
    "Tapping FeCR HC":           ("FECR", "entry", "tapping"),
    "Tapping Fe-Cr HC":          ("FECR", "entry", "tapping"),
    "Tapping FeCR MC":           ("FECRMC", "entry", "tapping"),
    "Tapping Fe-Cr MC":          ("FECRMC", "entry", "tapping"),
    "Tapping FeCr HC (Low Ti)":  ("FECRLTI", "entry", "tapping"),
    "Tapping FECrHC(lowTi)":     ("FECRLTI", "entry", "tapping"),
    "Tapping FeSi":              ("FESI", "entry", "tapping"),
    "Tapping Si Metal":          ("SIMET", "entry", "tapping"),
    "Tapping Si metal":          ("SIMET", "entry", "tapping"),
    "Tapping Mn.Metal":          ("MNMET", "entry", "tapping"),
    "Tapping Mn Metal":          ("MNMET", "entry", "tapping"),
    "Tapping Al Bar":            ("ALB", "entry", "tapping"),
    # added at the ladle furnace
    "Alloy Scrap addition (Reformer tube/ Drum loose scrap)": ("ASC", "lrf", "process"),
    "LF Lime":           ("LIME", "lrf", "process"),
    "Si-Mn":             ("SIMN", "lrf", "process"),
    "Fe-Mn HC":          ("FEMNHC", "lrf", "process"),
    "Fe-Mn MC":          ("FEMNMC", "lrf", "process"),
    "Fe-Mn LC":          ("FEMNLC", "lrf", "process"),
    "ELC - Si Mn":       ("ELCSIMN", "lrf", "process"),
    "ELC SiMn":          ("ELCSIMN", "lrf", "process"),
    "Sul. Stick":        ("SULST", "lrf", "process"),
    "FeCr Lc":           ("FECRLC", "lrf", "process"),
    "Fe-Cr LC":          ("FECRLC", "lrf", "process"),
    "FeCr HC":           ("FECR", "lrf", "process"),
    "Fe-Cr HC":          ("FECR", "lrf", "process"),
    "FeCr MC":           ("FECRMC", "lrf", "process"),
    "Fe-Cr MC":          ("FECRMC", "lrf", "process"),
    "FeCr HC (Low Ti)":  ("FECRLTI", "lrf", "process"),
    "FeSi":              ("FESI", "lrf", "process"),
    "Si metal":          ("SIMET", "lrf", "process"),
    "Mn. Metal":         ("MNMET", "lrf", "process"),
    "Fe Moly":           ("FEMO", "lrf", "process"),
    "FeAl":              ("FEAL", "lrf", "process"),
    "FeV":               ("FEV", "lrf", "process"),
    "FeTi":              ("FETI", "lrf", "process"),
    "FeTi Wire (mtr)":   ("FETIW", "lrf", "process"),
    "FeB":               ("FEB", "lrf", "process"),
    "Spar":              ("FLSP", "lrf", "process"),
    "Almix/ Dolo mite":  ("ALMIX", "lrf", "process"),
    "Cu Scrap":          ("CU", "lrf", "process"),
    "Al Bar":            ("ALB", "lrf", "process"),
    "S-Wire":            ("SWIRE", "lrf", "process"),
    "Gr Wire (mtr.)":    ("GRWIRE", "lrf", "process"),
    "Al Wire (mtr.)":    ("ALWIRE", "lrf", "process"),
    "Ca-Si Wire":        ("CASI", "lrf", "process"),
    "CA-FE-AL Wire 13 mm": ("CAFEAL", "lrf", "process"),
    "Ca Wire 9 mm":      ("CAWIRE9", "lrf", "process"),
    "Lead Wire":         ("PBWIRE", "lrf", "process"),
    "FeSi Fines":        ("FESIF", "lrf", "process"),
    "Ni Plate":          ("NI", "lrf", "process"),
    "Nickle scrap/Reformer tube": ("NISCR", "lrf", "process"),
    "CPC LRF":           ("CARB", "lrf", "process"),
    "Met Coke":          ("METCOKE", "lrf", "process"),
    "Inject /hard Coke": ("HCOKE", "lrf", "process"),
    "Radex":             ("RADEX", "lrf", "process"),
    "Al shots":          ("ALSHOT", "lrf", "process"),
    "Fe. Nb":            ("FENB", "lrf", "process"),
    "Sy. Slag":          ("SSLAG", "lrf", "process"),
    "Gr. Fines":         ("GRF", "lrf", "process"),
    "LCC":               ("LCC", "lrf", "process"),
    "Fe-P":              ("FEP", "lrf", "process"),
    "Nitrate Mn WIRE":   ("NMNWIRE", "lrf", "process"),
    "Pet Coke Dololime Top": ("PETCOKE", "lrf", "process"),
    "Mo-Calcium (Mo-Metal )": ("MOCA", "lrf", "process"),
    # ── ZPF CONSP spellings of the same consumables ──────────────────────
    "FeCrHC (Low Ti)":   ("FECRLTI", "lrf", "process"),
    "Almix/ Dolo- mite": ("ALMIX", "lrf", "process"),
    "S- Wire":           ("SWIRE", "lrf", "process"),
    "Gr. Wire (mtr.)":   ("GRWIRE", "lrf", "process"),
    "Inject/ hard Coke": ("HCOKE", "lrf", "process"),
    "Nitrate Mn":        ("NMNWIRE", "lrf", "process"),
}

# Caster yield-loss streams (the trailing block of the consumption sheets).
CASTER_LOSSES = {
    "Front & End Cut & blt Rjt (9SCRAP_INTERNAL & 9CBLSLVENDS)": "cut_reject",
    "Tundish Skull (9_TUNDISH_SKULL)": "tundish_skull",
    "SAMPLE WT (9SLBBLTENDS)":         "sample_wt",
    "Ladle Skull (9STEELSKULL)":       "ladle_skull",
    "Slag Pit":                        "slag_pit",
    "B/O":                             "burn_off",
    "LM returned (Others)":            "lm_returned",
    "Scale & Cut Loss (INH_MLSL_FINES)": "scale_cut_loss",
    # ZPF CONSP writes the same loss streams without the SAP codes
    "Front & End Cut & blt Rjt": "cut_reject",
    "Tundish Skull":             "tundish_skull",
    "SampleWT":                  "sample_wt",
    "Ladle Skull":               "ladle_skull",
    "Scale & Cut Loss":          "scale_cut_loss",
}

# Headers deliberately ignored: bookkeeping, duplicates, or values the engine
# recomputes. Listed so --dry-run's "unmapped" report stays meaningful.
IGNORE = {
    "Sl. No.", "Sl.No.", "Date", "Heat No.", "Heat No", "Grade", "Shift",
    "HM %", "DRI %", "Total Charge Wt.", "Column1", "Column2", "Column3",
    "Size", "Actual Cast Time", "Actual Cast Time (Min.)", "LRF Process Time",
    "Delay (mins)", "Delay (mins)2", "Delay Duration", "Agency",
    "Delay Code", "Delay code", "Delay Description", "delay description",
    "Delay Reason", "Delay Start Time", "Delay End Time", "Delay Start",
    "Delay End", "Nitrogen (N2)",
}

# The plant's own agency spellings, collapsed onto the canonical delay-master
# set (see app/delay_codes_data.py). "No Delay" is a sentinel meaning the heat
# ran clean — it must never become a Delay record.
NO_DELAY = {"no delay", "nodelay", "no delays", "none"}
AGENCY_CANON = {
    "planned_shutdown": "Planned Shutdown",
    "planned shutdown": "Planned Shutdown",
    "eaf_operations": "EAF Operations", "eaf_operation": "EAF Operations",
    "eaf operations": "EAF Operations", "eaf operation": "EAF Operations",
    "zpf_operations": "ZPF Operations", "zpf_operation": "ZPF Operations",
    "zpf operations": "ZPF Operations", "zpf operation": "ZPF Operations",
    "lrf_operation": "LRF Operations", "lrf_operations": "LRF Operations",
    "lrf_vd": "LRF / VD", "lrf-vd": "LRF / VD", "lrf/vd": "LRF / VD",
    "lrf_vd_operation": "LRF / VD",
    "ccm": "CCM Operations", "ccm_operations": "CCM Operations",
    "slag_pit": "Slag Pit", "slag pit": "Slag Pit",
    "crane": "Crane", "external": "External", "mechanical": "Mechanical",
    "electrical": "Electrical", "refractory": "Refractory",
    "utility": "Utility", "ppc": "PPC",
}


def canon_agency(v) -> str:
    s = norm_head(v)
    return AGENCY_CANON.get(s.lower(), s)


def canon_unit(v, kind):
    """Normalise machine identifiers onto the codes the app uses.

    The workbook writes the same unit several ways: the consumption sheets put
    a bare 5 or 6 in "LRF" and 2.1 / 3.1 in "VD/Tank No", while LRFVD writes
    "LRF6" as text. Left raw, a heat's route reads "2.1" and never matches the
    Gantt's VD-2 / VD-3 lanes.  kind: "lrf" | "vd" | "ccm"
    """
    s = norm_head(v).upper().replace(" ", "")
    if not s:
        return None
    # A value carrying ANOTHER unit's prefix is not this unit. Without this a
    # bare digit search reads the "2" out of "BC-2" and files a caster-routed
    # heat onto a VD tank.
    prefixes = {"lrf": "LRF", "vd": "VD", "ccm": ("BC", "CCM")}
    other = {p for k, p in prefixes.items() if k != kind}
    for grp in other:
        for p in (grp if isinstance(grp, tuple) else (grp,)):
            if s.startswith(p):
                return None
    digits = re.findall(r"\d", s)
    n = digits[0] if digits else None
    if kind == "lrf":
        return f"LRF-{n}" if n in ("5", "6") else None
    if kind == "vd":
        # The plant tracks two degassers, VD-2 and VD-3. The workbook writes
        # them several ways — "VD-2", a bare "2", or a tank-level "2.1" from
        # when the tanks were listed separately — and all of them name one of
        # the two, so the sub-tank digit is dropped rather than the row.
        return f"VD-{n}" if n in ("2", "3") else None
    if kind == "ccm":
        return f"BC-{n}" if n in ("2", "3") else None
    return None
