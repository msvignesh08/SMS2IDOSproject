"""
SMS-2 IDOS — read the plant's historical workbook (IDOS.xlsx)
==============================================================
The SPREADSHEET half of the history import. It parses the workbook into a
portable structure; writing that structure to the database is history_writer's
job, which the hosted server also uses.

    # look before you leap — reports coverage, writes nothing
    python tools/import_history.py "E:/Projects/Assets/IDOS.xlsx" --dry-run

    # load straight into the local database
    python tools/import_history.py "E:/Projects/Assets/IDOS.xlsx"

    # produce the committed bundle the live server replays
    python tools/import_history.py "E:/Projects/Assets/IDOS.xlsx" --export

Requires openpyxl, which is a DEVELOPER dependency: the running application
and the hosted seeder never read .xlsx, they replay the bundle instead.
"""
from __future__ import annotations

import argparse
import gzip
import sys
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models as M                                       # noqa: E402
from app.core.database import SessionLocal, engine, Base          # noqa: E402
from tools import history_map as HM                               # noqa: E402
from tools.history_writer import HistoryWriter, dt_of, to_bundle  # noqa: E402

DEFAULT_BUNDLE = Path(__file__).resolve().parent.parent / "app" / "data" / "history.json.gz"


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


# ── mapping one row onto stage data ───────────────────────────────────────

def apply_fields(row, fmap, day, out, unmapped=None):
    """Write mapped values into out[stage][key]; record unmapped headers."""
    for head, val in row.items():
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
        out_mats.append([entry_stage if hint == "entry" else hint, code, qty, point])


# ── gathering ─────────────────────────────────────────────────────────────

class Importer:
    """Parses the workbook into {heat_no: {...}} — no database involved."""

    def __init__(self, wb):
        self.wb = wb
        self.unmapped = Counter()
        self.agencies = Counter()
        self.stats = Counter()
        # heat_no -> {"day","shift","entry","grade","data","mats","delays"}
        self.heats = {}

    def slot(self, hno, day=None, entry=None, grade=None, shift=None):
        h = self.heats.get(hno)
        if not h:
            h = {"day": None, "shift": None, "grade": None,
                 "entry": entry or ("zpf" if hno.startswith("F") else "eaf"),
                 "data": defaultdict(dict), "mats": [], "delays": []}
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
            apply_fields(row, {**fields, **HM.LRF_BAND_FIELDS},
                         h["day"] or day, h["data"], self.unmapped)
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
            h = self.slot(hno, day, shift=HM.norm_head(row.get("Shift")))
            apply_fields(row, fields, h["day"] or day, h["data"], self.unmapped)
            collect_materials(row, h["entry"], h["mats"])
            self.stats[f"rows:{sheet}"] += 1

    def gather_delays(self, sheet, fields, stage_hint):
        """The delay sheets carry BOTH cycle fields and the delay event."""
        for row in read_sheet(self.wb, sheet):
            hno = heat_no_of(row)
            if not hno:
                continue
            day = HM.as_date(row.get("Date"))
            h = self.slot(hno, day, shift=HM.norm_head(row.get("Shift")))
            apply_fields(row, fields, h["day"] or day, h["data"], self.unmapped)
            agency = HM.canon_agency(row.get("Agency"))
            if not agency:
                continue
            self.agencies[agency] += 1
            if agency.strip().lower() in HM.NO_DELAY:
                self.stats["delays: 'no delay' rows skipped"] += 1
                continue
            dur = HM.as_num(row.get("Delay Duration"))
            if dur is None or dur <= 0:
                # LRFVD/BC "Delay Duration" is a variance against standard time
                # and is routinely negative — that is not a delay event.
                self.stats["delays: non-positive skipped"] += 1
                continue
            h["delays"].append({
                "stage": h["entry"] if stage_hint == "entry" else stage_hint,
                "agency": agency,
                "code": HM.norm_head(row.get("Delay Code") or row.get("Delay code")
                                     or row.get("Delay Reason")),
                "desc": HM.norm_head(row.get("Delay Description")
                                     or row.get("delay description")
                                     or row.get("Description")),
                "min": dur,
                "start": HM.as_time(row.get("Delay Start Time") or row.get("Delay Start")),
                "end": HM.as_time(row.get("Delay End Time") or row.get("Delay End")),
                "day": h["day"] or day})
            self.stats["delays: collected"] += 1

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

    def report(self):
        pref = Counter(h[0] for h in self.heats)
        entry = Counter(v["entry"] for v in self.heats.values())
        days = [v["day"] for v in self.heats.values() if v["day"]]
        print(f"\nHEATS FOUND: {len(self.heats)}   prefixes={dict(pref)}   entry={dict(entry)}")
        print(f"  with a usable date: {len(days)}"
              + (f"   window {min(days)} .. {max(days)}" if days else ""))
        if days and (min(days) < HM.WINDOW[0] or max(days) > HM.WINDOW[1]):
            print(f"  !! dates fall outside the workbook's real window {HM.WINDOW}"
                  f" — check the day/month reading")
        for k in sorted(self.stats):
            print(f"  {k}: {self.stats[k]}")
        print(f"\nDELAY AGENCIES SEEN: {dict(self.agencies.most_common())}")
        if self.unmapped:
            print(f"\nUNMAPPED COLUMNS ({len(self.unmapped)}) — add to history_map.py if wanted:")
            for h, n in self.unmapped.most_common(40):
                print(f"   {n:>5}  {h}")
        else:
            print("\nUNMAPPED COLUMNS: none — every column has a home.")


def main():
    ap = argparse.ArgumentParser(description="Read IDOS.xlsx production history.")
    ap.add_argument("xlsx", nargs="?", default=r"E:/Projects/Assets/IDOS.xlsx")
    ap.add_argument("--dry-run", action="store_true",
                    help="analyse and report; write nothing")
    ap.add_argument("--export", nargs="?", const=str(DEFAULT_BUNDLE), default=None,
                    metavar="PATH",
                    help="write the portable bundle the hosted seeder replays "
                         f"(default: {DEFAULT_BUNDLE.name})")
    a = ap.parse_args()

    wb = openpyxl.load_workbook(a.xlsx, data_only=True)
    imp = Importer(wb)
    imp.gather_all()
    imp.report()

    if a.export:
        out = Path(a.export)
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = to_bundle(imp.heats, Path(a.xlsx).name)
        out.write_bytes(gzip.compress(raw.encode("utf-8")))
        print(f"\nEXPORTED {len(imp.heats)} heats → {out}  "
              f"({out.stat().st_size / 1024:.0f} KB gzipped)")
        print("Commit it, then on the server run:  python tools/seed_history.py")
        return

    if a.dry_run:
        print("\nDRY RUN — nothing written.")
        return

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        w = HistoryWriter(db)
        n = w.write(imp.heats, Path(a.xlsx).name)
        for k in sorted(w.stats):
            print(f"  {k}: {w.stats[k]}")
        if w.unknown_mat:
            print(f"\n!! material codes not in the master: {dict(w.unknown_mat)}")
        if w.unknown_agency:
            print(f"!! agencies with no delay-master entry: {dict(w.unknown_agency)}")
        db.add(M.AuditLog(
            user_name="History Import", category="admin", action="history_imported",
            detail=f"{n} heats backfilled from {Path(a.xlsx).name}. "
                   f"Historical records — no approval signatures."))
        db.commit()
        print(f"\nIMPORTED {n} heats.")
        print("Restart the server if the Gantt looks stale (SQLite WAL).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
