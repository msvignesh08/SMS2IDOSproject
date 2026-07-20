"""
Regenerate app/delay_codes_data.py from the company's delay-codes spreadsheet.

Usage:
    python tools/gen_delay_codes.py "E:/Projects/Assets/Delay Codes for Reference.xlsx"

The sheet has one tab per stage (EAF, ZPF, LRF, BC2, BC3). On each tab a header
row starts with the label "Agency"; the cells to its right are the AGENCIES,
and each agency's reasons are listed vertically beneath it. This script reads
that shape and emits a flat, de-duplicated (stage, agency, reason) table.

Run this whenever the plant revises the spreadsheet, then reseed a fresh DB.
Requires openpyxl (dev-only; the app itself never reads the .xlsx).
"""
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path

import openpyxl

# One spreadsheet tab may feed more than one app stage. There is no separate VD
# tab — vacuum degassing is refining like the LRF, so it reuses the LRF codes.
# Both billet casters map to the single `caster` stage (merged, de-duplicated).
SHEET_TO_STAGES = {
    "EAF": ["eaf"], "ZPF": ["zpf"], "LRF": ["lrf", "vd"],
    "BC2": ["caster"], "BC3": ["caster"],
}
STAGE_ORDER = ["eaf", "zpf", "lrf", "vd", "caster"]

# The spreadsheet spells the same agency several ways across tabs; collapse each
# to one clean, canonical label so the dropdown is consistent.
_AGENCY_CANON = {
    "planned_shutdown": "Planned Shutdown", "planned shutdown": "Planned Shutdown",
    "eaf_operations": "EAF Operations", "eaf operations": "EAF Operations", "eaf operation": "EAF Operations",
    "zpf_operations": "ZPF Operations", "zpf operations": "ZPF Operations", "zpf operation": "ZPF Operations",
    "lrf operation": "LRF Operations", "lrf_operations": "LRF Operations",
    "lrf_vd": "LRF / VD", "lrf/vd": "LRF / VD",
    "ccm": "CCM Operations", "ccm_operations": "CCM Operations",
    "slag_pit": "Slag Pit", "slag pit": "Slag Pit",
    "crane": "Crane",
}


def _norm(s):
    return re.sub(r"\s+", " ", str(s).strip()) if s not in (None, "") else ""


def _canon_agency(a):
    a = _norm(a)
    return _AGENCY_CANON.get(a.lower(), a)


def parse_sheet(ws):
    """Return {agency: [reason, ...]} for one worksheet."""
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = agency_col = None
    for i, row in enumerate(rows):
        for j, c in enumerate(row):
            if _norm(c).lower() == "agency":
                hdr_i, agency_col = i, j
                break
        if hdr_i is not None:
            break
    if hdr_i is None:
        return {}
    header = rows[hdr_i]
    cols = [(j, _canon_agency(header[j]))
            for j in range(agency_col + 1, len(header))
            if _norm(header[j]) and _norm(header[j]).lower() != "agency"]
    out = OrderedDict()
    for j, name in cols:
        seen, reasons = set(), []
        for r in rows[hdr_i + 1:]:
            v = _norm(r[j]) if j < len(r) else ""
            if v and v.lower() not in ("yes", "no") and v.lower() not in seen:
                seen.add(v.lower())
                reasons.append(v)
        if reasons:
            out.setdefault(name, [])
            out[name].extend(reasons)
    return out


def build(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    sheets = {ws.title: parse_sheet(ws) for ws in wb.worksheets}
    triples, seen = [], set()
    for stage in STAGE_ORDER:
        for sheet, stages in SHEET_TO_STAGES.items():
            if stage not in stages or sheet not in sheets:
                continue
            for agency, reasons in sheets[sheet].items():
                for reason in reasons:
                    key = (stage, agency.lower(), reason.lower())
                    if key in seen:
                        continue
                    seen.add(key)
                    triples.append((stage, agency, reason))
    return triples


def emit(triples, out_path):
    head = '''"""
SMS-2 IDOS — COMPANY DELAY CODES  (auto-generated)
===================================================
Source: "Delay Codes for Reference.xlsx" — the plant's own delay taxonomy,
organised per stage -> AGENCY -> reason. The Delay Tracking dialog reads this as
two cascading dropdowns: pick the Agency, then its Reason.

Regenerate with tools/gen_delay_codes.py after the company revises the sheet.
DO NOT hand-edit — edits here are lost on the next regeneration.

Each row: (stage, agency, reason).  stage in {eaf, zpf, lrf, vd, caster}.
"""

DELAY_CODES = [
'''
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(head)
        for s, a, r in triples:
            a_ = a.replace('"', '\\"')
            r_ = r.replace("\\", "\\\\").replace('"', '\\"')
            f.write(f'    ("{s}", "{a_}", "{r_}"),\n')
        f.write("]\n")


if __name__ == "__main__":
    xlsx = sys.argv[1] if len(sys.argv) > 1 else r"E:/Projects/Assets/Delay Codes for Reference.xlsx"
    out = Path(__file__).resolve().parent.parent / "app" / "delay_codes_data.py"
    rows = build(xlsx)
    emit(rows, out)
    stages = {}
    for s, a, _ in rows:
        stages.setdefault(s, set()).add(a)
    for s in STAGE_ORDER:
        if s in stages:
            n = sum(1 for x in rows if x[0] == s)
            print(f"  {s:8} agencies={len(stages[s]):2}  reasons={n}")
    print(f"Wrote {out}  ({len(rows)} rows)")
