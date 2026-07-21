"""
SMS-2 IDOS — SEED THE PLANT'S HISTORY INTO A HOSTED DEPLOYMENT
==============================================================
Run this ONCE after hosting, to load the real production history that came
from the plant's own workbook.

    python tools/seed_history.py

That is the whole command. It reads the bundle committed at
app/data/history.json.gz and writes it into whatever database the app itself
is configured to use — PostgreSQL when SMS2_DATABASE_URL is set, otherwise the
local SQLite file — so it behaves identically on your laptop, on Render, or on
a plant server. It imports the app's own config rather than reading the
environment itself, so the two can never disagree about which database is live.

WHY A BUNDLE AND NOT THE SPREADSHEET
    A hosted box has neither IDOS.xlsx nor openpyxl. The workbook is parsed
    once on a developer machine and the result committed, so the server just
    replays a verified payload. Regenerate it whenever the plant revises the
    sheet:

        python tools/import_history.py "path/to/IDOS.xlsx" --export

SAFE TO RE-RUN
    Heat numbers already present are skipped, never duplicated. If a deploy
    half-finishes, run it again.

ORDER
    Run it AFTER the app has started at least once, so the masters (materials,
    delay codes, users, heat series) exist — this seeder fills in production
    history, it does not create the reference data. It checks and tells you.
"""
from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import models as M                                  # noqa: E402
from app.core.database import SessionLocal, engine, Base     # noqa: E402
from tools.history_writer import HistoryWriter, from_bundle  # noqa: E402

BUNDLE = Path(__file__).resolve().parent.parent / "app" / "data" / "history.json.gz"


def load(path: Path):
    raw = path.read_bytes()
    if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return from_bundle(raw.decode("utf-8"))


def main():
    ap = argparse.ArgumentParser(
        description="Load the plant's production history into this deployment.")
    ap.add_argument("bundle", nargs="?", default=str(BUNDLE),
                    help=f"history bundle (default: {BUNDLE.name})")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written, change nothing")
    a = ap.parse_args()

    path = Path(a.bundle)
    if not path.exists():
        print(f"ERROR: no history bundle at {path}\n"
              f"Generate one on a machine that has the workbook:\n"
              f"    python tools/import_history.py \"path/to/IDOS.xlsx\" --export")
        return 1

    heats, meta = load(path)
    print(f"SMS-2 IDOS — history seeder")
    print(f"  bundle : {path.name}  ({path.stat().st_size / 1024:.0f} KB)")
    print(f"  source : {meta.get('source')}  generated {meta.get('generated')}")
    print(f"  heats  : {len(heats)}")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # The masters must exist first: without materials and delay codes the
        # additions and delays would silently land with nothing to point at.
        mats = db.query(M.Material).count()
        reasons = db.query(M.DelayReason).count()
        if not mats or not reasons:
            print(f"\nERROR: reference data is missing "
                  f"(materials={mats}, delay reasons={reasons}).\n"
                  f"Start the application once so it seeds the masters, then re-run this.")
            return 1

        already = db.query(M.Heat).filter_by(is_imported=True).count()
        if already:
            print(f"  note   : {already} imported heats are already present; "
                  f"those numbers will be skipped.")

        if a.dry_run:
            present = {h.heat_no for h in db.query(M.Heat.heat_no)}
            todo = [n for n in heats if n not in present]
            print(f"\nDRY RUN — would write {len(todo)} heats, "
                  f"skip {len(heats) - len(todo)} already present. Nothing changed.")
            return 0

        w = HistoryWriter(db)
        n = w.write(heats, meta.get("source") or path.name)
        for k in sorted(w.stats):
            print(f"  {k}: {w.stats[k]}")
        if w.unknown_mat:
            print(f"\n!! material codes not in the master: {dict(w.unknown_mat)}")
        if w.unknown_agency:
            print(f"!! agencies with no delay-master entry: {dict(w.unknown_agency)}")
        db.add(M.AuditLog(
            user_name="History Seeder", category="admin", action="history_imported",
            detail=f"{n} heats seeded from {meta.get('source')} bundle. "
                   f"Historical records — no approval signatures."))
        db.commit()
        print(f"\nSEEDED {n} heats.")
        print("Restart the web service if the Gantt looks stale (SQLite WAL).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
