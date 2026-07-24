"""
IMPORT — staged workbook upload, correction review, approval, then commit.

THE CORRECTION APPROVAL LAW
    An uploaded spreadsheet never reaches production data on its own. It is
    parsed, the correction engine proposes fixes for what is missing or
    malformed, and a human accepts or rejects each CLASS of fix before
    anything is written. That applies to every account — an administrator gets
    no shortcut past it, because the risk being managed is bad data, not
    insufficient privilege.

APPROVAL IS BY RULE, NOT BY CELL
    The plant's real workbook produces ~78,000 correctable findings. Clicking
    78,000 checkboxes is not review, it is a rubber stamp with extra steps.
    So findings are grouped by the RULE that produced them: one decision
    covers "round Excel float noise" (47,613 cells), another covers "fill the
    heat number on continuation rows" (14,307). Each group shows its count,
    its reasoning, and real samples drawn from the file, and every individual
    finding stays inspectable underneath.

THE FILE STAYS ON DISK
    70,000 staged rows do not belong in a database column. The workbook is
    written to data/imports/, aggregates go in the batch record, and the
    commit re-reads the file applying exactly the rules that were approved.
"""
from __future__ import annotations

import collections
import copy
import threading
import traceback
import gzip
import json
import os
import shutil
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from sqlalchemy.orm.attributes import flag_modified

from .. import models as M
from ..core import plant_time
from ..core.database import get_db
from ..engines import import_review as IR
from ..engines.lifecycle import audit
from .auth import get_current_user, require_perm

router = APIRouter(prefix="/api/imports", tags=["imports"])

STORE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "imports")
MAX_BYTES = 60 * 1024 * 1024
SAMPLES_PER_RULE = 8

# What each rule does, in the operator's language rather than the engine's.
RULE_TITLES = {
    "continuation_row": "Fill blank cells on continuation rows",
    "heat_no_format": "Rewrite heat numbers to one spelling",
    "float_noise": "Round Excel float noise",
    "epoch_time": "Recover times Excel rolled to 1900-01-01",
    "midnight_crossing": "Recompute durations that cross midnight",
    "negative_duration": "Negative durations that cannot be recovered",
    "formula_error": "Broken spreadsheet formulas (#REF!, #VALUE!)",
    "missing_mandatory": "Mandatory fields left empty",
    "missing_key": "Rows with no heat number to attach to",
    "heat_no_suspect": "Heat numbers with the wrong number of digits",
    "sheet_missing": "Expected sheet not present",
}


def _path(batch_id: int) -> str:
    return os.path.join(STORE, f"batch_{batch_id}.xlsx")


def _open(batch: M.ImportBatch):
    import openpyxl
    p = _path(batch.id)
    if not os.path.exists(p):
        raise HTTPException(410, "The uploaded file is no longer on the server. "
                                 "Upload it again to review it.")
    return openpyxl.load_workbook(p, data_only=True, read_only=True)


def _cache_path(batch_id: int) -> str:
    return os.path.join(STORE, f"batch_{batch_id}.analysis.json.gz")


def _analysis(batch: M.ImportBatch) -> dict:
    """The parsed result, analysed once and kept.

    Re-reading a 20 MB workbook takes ~30 seconds. Doing that again every time
    somebody opens a drill-down, and once more at commit, would make Inspect
    look like a hang. The analysis is written beside the file at upload and
    reused; it is derived data, so a missing cache just rebuilds it.
    """
    cp = _cache_path(batch.id)
    if os.path.exists(cp):
        try:
            with gzip.open(cp, "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass                       # corrupt cache: fall through and rebuild
    res = IR.analyse(_open(batch))
    try:
        with gzip.open(cp, "wt", encoding="utf-8") as f:
            json.dump(res, f, default=str)
    except OSError:
        pass                           # cache is an optimisation, not a store
    return res


def _group(issues: list) -> list[dict]:
    """Findings collapsed into the decisions a human actually makes."""
    by = collections.defaultdict(list)
    for i in issues:
        by[i["rule"]].append(i)
    out = []
    for rule, items in by.items():
        fixable = [i for i in items if i["proposed"] is not None]
        sev = ("error" if any(i["severity"] == "error" for i in items)
               else "warn" if any(i["severity"] == "warn" for i in items)
               else "info")
        out.append({
            "rule": rule,
            "title": RULE_TITLES.get(rule, rule.replace("_", " ").title()),
            "count": len(items),
            "fixable": len(fixable),
            "severity": sev,
            "basis": IR.basis_of(rule),
            "sheets": sorted({i["sheet"] for i in items}),
            "fields": sorted({i["label"] for i in items if i["label"]})[:8],
            "samples": [{"sheet": i["sheet"], "row": i["row_no"],
                         "heat_no": i["heat_no"], "field": i["label"],
                         "raw": i["raw_value"], "proposed": i["proposed"],
                         "message": i["message"]}
                        for i in (fixable or items)[:SAMPLES_PER_RULE]],
            # Nothing is pre-approved. A correction is applied only after
            # somebody says so.
            "status": "pending",
        })
    order = {"error": 0, "warn": 1, "info": 2}
    out.sort(key=lambda g: (0 if g["fixable"] else 1, order[g["severity"]], -g["count"]))
    return out


@router.get("")
def list_batches(user: M.User = Depends(get_current_user),
                 db: DBSession = Depends(get_db)):
    rows = (db.query(M.ImportBatch)
            .filter(M.ImportBatch.archived == False)          # noqa: E712
            .order_by(M.ImportBatch.id.desc()).limit(40).all())
    return {"batches": [{
        "id": b.id, "filename": b.filename, "size_bytes": b.size_bytes,
        "status": b.status, "stats": b.stats or {}, "sheets": b.sheets or {},
        "uploaded_by": b.uploaded_by_name,
        "uploaded_at": b.uploaded_at.isoformat(sep=" ") if b.uploaded_at else "",
        "committed_by": b.committed_by_name,
        "committed_at": b.committed_at.isoformat(sep=" ") if b.committed_at else "",
        "commit_result": b.commit_result or {},
    } for b in rows]}


@router.post("/upload")
async def upload(file: UploadFile = File(...),
                 user: M.User = Depends(require_perm("admin")),
                 db: DBSession = Depends(get_db)):
    """Take the workbook, stage it, analyse it. Writes no production data."""
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Upload an Excel workbook (.xlsx).")
    os.makedirs(STORE, exist_ok=True)
    b = M.ImportBatch(filename=file.filename or "workbook.xlsx",
                      status="uploaded", uploaded_by=user.id,
                      uploaded_by_name=user.name)
    db.add(b)
    db.commit()
    db.refresh(b)

    size = 0
    try:
        with open(_path(b.id), "wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "Workbook is larger than 60 MB.")
                out.write(chunk)
    except HTTPException:
        db.delete(b)
        db.commit()
        raise

    b.size_bytes = size
    b.status = "analysing"
    audit(db, user, "admin", "import_uploaded",
          detail=f"{b.filename} · {size // 1048576} MB")
    db.commit()

    # ANALYSE OUT OF BAND, NOT INSIDE THIS REQUEST.
    #
    # Reading the plant's real 20 MB workbook takes ~50 s here and several
    # minutes on a small shared instance, and peaks around 280 MB. Doing that
    # inside the upload meant the connection was closed — by the host's
    # timeout or an out-of-memory kill — before any reply was written, so the
    # browser received a truncated body and reported "Unexpected end of JSON
    # input". The upload now returns immediately and the client polls.
    threading.Thread(target=_analyse_batch, args=(b.id,), daemon=True).start()
    return {"id": b.id, "filename": b.filename, "size_bytes": size,
            "status": "analysing", "stats": {}, "sheets": {}}


def _analyse_batch(batch_id: int) -> None:
    """Background analysis. Owns its own session — the request's is long gone."""
    from ..core.database import SessionLocal
    db = SessionLocal()
    try:
        b = db.get(M.ImportBatch, batch_id)
        if not b:
            return
        try:
            res = _analysis(b)
            b.sheets = res["sheets"]
            b.stats = {**res["stats"], "groups": _group(res["issues"])}
            b.status = "analysed"
        except Exception as e:                                # noqa: BLE001
            # Record WHY, so the screen can say it instead of showing a
            # spinner that never resolves.
            b.status = "failed"
            b.stats = {"error": f"{type(e).__name__}: {e}"[:400]}
            traceback.print_exc()
        db.commit()
    finally:
        db.close()


@router.get("/{batch_id}")
def get_batch(batch_id: int, user: M.User = Depends(get_current_user),
              db: DBSession = Depends(get_db)):
    b = db.get(M.ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "No such import batch.")
    return {"id": b.id, "filename": b.filename, "status": b.status,
            "sheets": b.sheets or {}, "stats": b.stats or {},
            "uploaded_by": b.uploaded_by_name,
            "uploaded_at": b.uploaded_at.isoformat(sep=" ") if b.uploaded_at else "",
            "commit_result": b.commit_result or {}}


@router.get("/{batch_id}/findings")
def findings(batch_id: int, rule: str = "", sheet: str = "",
             offset: int = 0, limit: int = 100,
             user: M.User = Depends(get_current_user),
             db: DBSession = Depends(get_db)):
    """Every individual finding behind a group — the drill-down.

    Re-read from the file rather than stored: a hundred thousand findings do
    not belong in a database column, and the file is right there.
    """
    b = db.get(M.ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "No such import batch.")
    res = _analysis(b)
    items = [i for i in res["issues"]
             if (not rule or i["rule"] == rule) and (not sheet or i["sheet"] == sheet)]
    return {"total": len(items),
            "findings": items[offset:offset + min(limit, 500)]}


class DecideIn(BaseModel):
    rule: str
    decision: str          # approved | rejected | pending


@router.post("/{batch_id}/decide")
def decide(batch_id: int, body: DecideIn,
           user: M.User = Depends(get_current_user),
           db: DBSession = Depends(get_db)):
    """Accept or reject one class of correction.

    Deliberately NOT admin-only. The people who can tell a real continuation
    row from lost data are the shift engineers who wrote the sheet, and
    reserving the judgement for whoever holds the admin password would put the
    decision in the wrong hands. The decision is recorded against the person
    who made it, which is what the audit needs.
    """
    if body.decision not in ("approved", "rejected", "pending"):
        raise HTTPException(400, "Decision must be approved, rejected or pending.")
    b = db.get(M.ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "No such import batch.")
    if b.status == "committed":
        raise HTTPException(400, "This batch is already committed — its "
                                 "corrections can no longer be changed.")
    # DEEP copy, then flag_modified. dict()/list() are shallow, so the group
    # dicts stay shared with b.stats — mutating one mutates the other, the
    # reassignment then looks like a no-op, and SQLAlchemy never writes the
    # decision. The endpoint returned 200 and the approval silently vanished.
    stats = copy.deepcopy(dict(b.stats or {}))
    groups = list(stats.get("groups") or [])
    hit = next((g for g in groups if g["rule"] == body.rule), None)
    if not hit:
        raise HTTPException(404, f"No finding group “{body.rule}” in this batch.")
    if not hit["fixable"] and body.decision == "approved":
        raise HTTPException(400, "Nothing in this group can be auto-corrected — "
                                 "it is reported for you to fix at source.")
    hit["status"] = body.decision
    hit["decided_by"] = user.name
    hit["decided_at"] = plant_time.plant_now().isoformat(sep=" ")
    stats["groups"] = groups
    b.stats = stats
    flag_modified(b, "stats")       # JSON columns need the change declared
    audit(db, user, "admin", "import_correction_decided",
          detail=f"batch {b.id} · {body.rule} · {body.decision} "
                 f"({hit['fixable']} corrections)")
    db.commit()
    return {"ok": True, "group": hit}


@router.post("/{batch_id}/commit")
def commit(batch_id: int, user: M.User = Depends(require_perm("admin")),
           db: DBSession = Depends(get_db)):
    """Write the approved result into production heats.

    Re-reads the workbook and applies ONLY the rules that were approved.
    Rejected rules leave their raw values exactly as the sheet had them; a row
    that then fails a mandatory check is skipped and counted, never guessed.
    """
    b = db.get(M.ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "No such import batch.")
    if b.status == "committed":
        raise HTTPException(400, "Already committed.")
    groups = (b.stats or {}).get("groups") or []
    undecided = [g["rule"] for g in groups if g["fixable"] and g["status"] == "pending"]
    if undecided:
        raise HTTPException(400, "Decide every correction group first — still "
                                 "pending: " + ", ".join(undecided))
    approved = {g["rule"] for g in groups if g["status"] == "approved"}

    res = _analysis(b)
    result = _write(db, res, approved, user)

    b.status = "committed"
    b.committed_by = user.id
    b.committed_by_name = user.name
    b.committed_at = plant_time.plant_now()
    b.commit_result = result
    audit(db, user, "admin", "import_committed",
          detail=f"batch {b.id} · {result['heats_written']} heats · "
                 f"{result['skipped']} skipped · rules approved: "
                 f"{', '.join(sorted(approved)) or 'none'}")
    db.commit()
    return result


def _write(db: DBSession, res: dict, approved: set, user: M.User) -> dict:
    """Turn staged rows into heats, honouring the approval decisions.

    A row whose only path to validity ran through a REJECTED rule is skipped
    and counted. That is the point of rejecting it.
    """
    # Which (sheet,row,field) did a rejected rule touch? Those revert to raw.
    reverted = {(i["sheet"], i["row_no"], i["field"])
                for i in res["issues"]
                if i["proposed"] is not None and i["rule"] not in approved}

    by_heat: dict[str, dict] = {}
    skipped = 0
    for r in res["rows"]:
        hn, sheet, row_no = r.get("heat_no"), r["_sheet"], r["_row"]
        if not hn or (sheet, row_no, "heat_no") in reverted:
            skipped += 1
            continue
        if not r.get("plant_day") or (sheet, row_no, "plant_day") in reverted:
            skipped += 1
            continue
        h = by_heat.setdefault(hn, {"heat_no": hn, "plant_day": r["plant_day"],
                                    "shift": r.get("shift"),
                                    "entry": None, "stages": {}, "delays": []})
        if r.get("_entry"):
            h["entry"] = r["_entry"]
        if r.get("shift") and not h["shift"]:
            h["shift"] = r["shift"]
        data = {k: v for k, v in r.items()
                if not k.startswith("_") and v not in (None, "")
                and k not in ("heat_no", "plant_day", "shift")
                and (sheet, row_no, k) not in reverted}
        st = h["stages"].setdefault(r["_stage"], {})
        st.update({k: v for k, v in data.items() if not k.startswith("delay_")})
        if data.get("delay_min") or data.get("agency"):
            h["delays"].append({"stage": r["_stage"],
                                "minutes": data.get("delay_min"),
                                "agency": data.get("agency"),
                                "code": data.get("delay_code"),
                                "start": data.get("delay_start"),
                                "end": data.get("delay_end")})

    existing = {x[0] for x in db.query(M.Heat.heat_no).all()}
    grades = {g.code.upper(): g for g in db.query(M.SteelGrade)}
    reasons = {(dr.category or "", dr.reason or ""): dr
               for dr in db.query(M.DelayReason)}
    written = already = 0

    for hn, h in by_heat.items():
        if hn in existing:
            already += 1
            continue
        gcode = None
        for stage in h["stages"].values():
            if stage.get("grade"):
                gcode = str(stage["grade"]).strip().upper()
                break
        grade = grades.get(gcode) if gcode else None
        if gcode and not grade:
            # A grade the masters have never seen is REGISTERED, not dropped —
            # the plant rolls new grades and the import must not lose the heat.
            grade = M.SteelGrade(code=gcode, name=gcode)
            db.add(grade)
            db.flush()
            grades[gcode] = grade
        heat = M.Heat(heat_no=hn, grade_id=grade.id if grade else None,
                      status="locked", entry_stage=h["entry"] or "eaf",
                      plant_day=date.fromisoformat(h["plant_day"]),
                      shift=h["shift"] or "A", created_by=user.id,
                      is_imported=True, locked_at=plant_time.plant_now())
        db.add(heat)
        db.flush()
        for stage, data in h["stages"].items():
            db.add(M.StageRecord(heat_id=heat.id, stage=stage, status="submitted",
                                 data=data, plant_day=heat.plant_day,
                                 shift=heat.shift, submitted_by=user.id))
        for d in h["delays"]:
            if not d.get("minutes"):
                continue
            dr = reasons.get((d.get("agency") or "", d.get("code") or ""))
            db.add(M.Delay(heat_id=heat.id, stage=d["stage"],
                           start_time=plant_time.plant_now(),
                           duration_min=float(d["minutes"]),
                           reason_id=dr.id if dr else None,
                           remarks=f"{d.get('agency') or ''} · {d.get('code') or ''}".strip(" ·")))
        written += 1
        if written % 500 == 0:
            db.flush()

    # ADVANCE THE HEAT SERIES PAST WHAT WE JUST IMPORTED.
    #
    # Without this the plant cannot create another heat: the series still
    # points at 260151 while the import has written E263853, so the next
    # operator to press "New heat" gets UNIQUE constraint failed on
    # heats.heat_no and production entry stops dead. Caught by the regression
    # suite, not by inspection.
    advanced = {}
    for series in db.query(M.HeatSeries):
        pfx = (series.prefix or "").upper()
        top = 0
        for hn in by_heat:
            # ONLY canonical-length numbers move the counter. The real
            # workbook holds 51 seven-digit typos like E2261255; letting one
            # of those set the series pushed the plant's next heat to
            # E2629368 and permanently broke its numbering.
            if hn.startswith(pfx) and len(hn) - len(pfx) == IR.CANON_HEAT_DIGITS:
                try:
                    top = max(top, int(hn[len(pfx):]))
                except ValueError:
                    pass
        if top >= series.next_number:
            advanced[pfx] = (series.next_number, top + 1)
            series.next_number = top + 1

    db.commit()
    return {"heats_written": written, "already_present": already,
            "series_advanced": {k: {"from": v[0], "to": v[1]}
                                for k, v in advanced.items()},
            "skipped": skipped, "rules_approved": sorted(approved),
            "rules_rejected": sorted({i["rule"] for i in res["issues"]
                                      if i["proposed"] is not None} - approved)}


@router.delete("/{batch_id}")
def discard(batch_id: int, user: M.User = Depends(require_perm("admin")),
            db: DBSession = Depends(get_db)):
    """Drop an uncommitted batch and its file. A committed one is kept —
    it is the record of where those heats came from."""
    b = db.get(M.ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "No such import batch.")
    if b.status == "committed":
        raise HTTPException(400, "A committed batch is the provenance record "
                                 "for its heats and is not deleted.")
    for f in (_path(b.id), _cache_path(b.id)):
        try:
            os.remove(f)
        except OSError:
            pass
    b.archived = True
    b.status = "discarded"
    audit(db, user, "admin", "import_discarded", detail=f"batch {b.id} · {b.filename}")
    db.commit()
    return {"ok": True}
