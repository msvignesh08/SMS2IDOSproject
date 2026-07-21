"""AOGE (Section 10) + SDGPCE (Section 11): users, roles, masters,
audit log access, backup, maintenance mode, system settings."""
from __future__ import annotations
import os, shutil, sqlite3
from datetime import datetime
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from ..core.database import get_db
from ..core import plant_time
from ..core.config import DATA_DIR, BACKUP_DIR, DATABASE_URL
from ..core.security import hash_password, verify_password, password_policy_errors
from .. import models as M
from ..engines import lifecycle as L
from ..engines import backup as BK
from ..engines.lifecycle import audit
from .auth import get_current_user, require_perm

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ═══ BUSINESS CONTINUITY DIRECTIVE (Constitution S.2) ═══
# The system shall NEVER reach zero active members of a protected class.
ADMIN_CLASS = {"Owner", "Super Administrator", "Administrator"}
PROTECTED_CLASSES = [
    ("Administrator-class (Owner/Super Administrator/Administrator)", ADMIN_CLASS),
    ("Shift Incharge (approver)", {"Shift Incharge"}),
    ("Shift Engineer", {"Shift Engineer"}),
]


def _continuity_check(db: DBSession, target: M.User, new_role_name: str | None,
                      deactivating: bool):
    """Raise 400 ACTION DENIED if the change would leave zero active members
    of any protected class the target currently belongs to."""
    cur_role = target.role.name
    for label, names in PROTECTED_CLASSES:
        if cur_role not in names:
            continue
        leaving = deactivating or (new_role_name is not None and new_role_name not in names)
        if not leaving:
            continue
        remaining = (db.query(M.User).join(M.Role)
                     .filter(M.Role.name.in_(list(names)),
                             M.User.active == True,          # noqa: E712
                             M.User.archived == False,       # noqa: E712
                             M.User.id != target.id).count())
        if remaining == 0:
            # BUSINESS CONTINUITY ALERT: the attempt itself is recorded and
            # broadcast — governance must know someone tried (commit survives
            # the raised denial by design).
            db.add(M.AuditLog(user_id=None, user_name="CONTINUITY-GUARD",
                              category="admin", action="continuity_denial",
                              detail=f"Blocked change on {target.employee_id}: "
                                     f"last active {label}"))
            db.add(M.Notification(kind="continuity", priority="high",
                                  title=f"Business continuity guard triggered",
                                  body=f"An attempt to remove the last active {label} "
                                       f"({target.employee_id} — {target.name}) was denied.",
                                  audience_role="Administrator"))
            db.commit()
            raise HTTPException(400,
                f"ACTION DENIED — Business Continuity: this is the last active "
                f"{label}. SMS-2 shall never reach zero active members of this "
                f"class (Constitution S.2). Assign another {label} first.")


def _reauth(db: DBSession, user: M.User, password: str):
    """Critical Operations Security (11.7): password re-authentication."""
    if not verify_password(password or "", user.password_hash):
        raise HTTPException(403, "Password re-authentication failed — critical operations require your password.")


# ─────────── Users & Roles ───────────

@router.get("/users")
def users(user: M.User = Depends(require_perm("admin")), db: DBSession = Depends(get_db)):
    return [{"id": u.id, "employee_id": u.employee_id, "name": u.name,
             "designation": u.designation, "department": u.department,
             "role": u.role.name, "role_id": u.role_id,
             "active": u.active, "archived": u.archived}
            for u in db.query(M.User).order_by(M.User.employee_id)]


class UserIn(BaseModel):
    employee_id: str
    name: str
    designation: str = ""
    department: str = ""
    role_id: int
    password: str
    reauth_password: str


@router.post("/users")
def create_user(body: UserIn, user: M.User = Depends(require_perm("admin")),
                db: DBSession = Depends(get_db)):
    _reauth(db, user, body.reauth_password)
    errs = password_policy_errors(body.password)
    if errs:
        raise HTTPException(400, " ".join(errs))
    emp = body.employee_id.strip().upper()
    if db.query(M.User).filter_by(employee_id=emp).first():
        raise HTTPException(400, f"Employee ID {emp} already exists")
    u = M.User(employee_id=emp, name=body.name, designation=body.designation,
               department=body.department, role_id=body.role_id,
               password_hash=hash_password(body.password))
    db.add(u)
    audit(db, user, "admin", "user_created", detail=emp)
    try:
        from ..engines.credentials import record
        record(db, emp, body.password)
    except Exception:
        pass
    L.notify(db, f"User account created: {emp}",
             f"By {user.name}.", kind="admin", audience_role="Administrator")
    db.commit()
    return {"ok": True, "id": u.id}


class UserPatch(BaseModel):
    role_id: int | None = None
    active: bool | None = None
    archived: bool | None = None
    reset_password: str | None = None
    reauth_password: str


@router.patch("/users/{uid}")
def patch_user(uid: int, body: UserPatch,
               user: M.User = Depends(require_perm("admin")),
               db: DBSession = Depends(get_db)):
    _reauth(db, user, body.reauth_password)
    u = db.get(M.User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    # Business continuity guards (Constitution S.2) BEFORE any change
    new_role_name = None
    if body.role_id is not None and body.role_id != u.role_id:
        nr = db.get(M.Role, body.role_id)
        new_role_name = nr.name if nr else None
    deactivating = (body.active is False) or (body.archived is True)
    if new_role_name is not None or deactivating:
        _continuity_check(db, u, new_role_name, deactivating)
    changes = []
    if body.role_id is not None:
        u.role_id = body.role_id; changes.append("role")
    if body.active is not None:
        u.active = body.active; changes.append(f"active={body.active}")
    if body.archived is not None:
        u.archived = body.archived   # archival, never deletion (10.13)
        if body.archived:
            u.active = False
        changes.append(f"archived={body.archived}")
    if body.reset_password:
        errs = password_policy_errors(body.reset_password)
        if errs:
            raise HTTPException(400, " ".join(errs))
        u.password_hash = hash_password(body.reset_password)
        changes.append("password_reset")
    audit(db, user, "admin", "user_modified", detail=f"{u.employee_id}: {', '.join(changes)}")
    db.commit()
    return {"ok": True}


@router.get("/roles")
def roles(user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    return [{"id": r.id, "name": r.name, "permissions": r.permissions,
             "is_system": r.is_system, "description": r.description}
            for r in db.query(M.Role).filter_by(archived=False)]


class RoleIn(BaseModel):
    name: str
    description: str = ""
    permissions: list[str]
    reauth_password: str


@router.post("/roles")
def create_role(body: RoleIn, user: M.User = Depends(require_perm("admin")),
                db: DBSession = Depends(get_db)):
    """Dynamic role creation without source code modification (10.4)."""
    _reauth(db, user, body.reauth_password)
    if db.query(M.Role).filter_by(name=body.name).first():
        raise HTTPException(400, "Role name already exists")
    r = M.Role(name=body.name, description=body.description,
               permissions=body.permissions, is_system=False)
    db.add(r)
    audit(db, user, "admin", "role_created", detail=body.name)
    db.commit()
    return {"ok": True, "id": r.id}


# ─────────── Masters ───────────

@router.get("/masters")
def masters(user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    grades = db.query(M.SteelGrade).filter_by(archived=False).all()
    return {
        "grades": [{"id": g.id, "code": g.code, "name": g.name,
                    "specs": {s.element: {"min": s.min_pct, "aim": s.aim_pct,
                                          "max": s.max_pct} for s in g.specs}}
                   for g in grades],
        "materials": [{"id": m.id, "code": m.code, "name": m.name,
                       "category": m.category, "unit": m.unit,
                       "composition": m.composition}
                      for m in db.query(M.Material).filter_by(archived=False)],
        "equipment": [{"id": e.id, "code": e.code, "kind": e.kind, "active": e.active}
                      for e in db.query(M.Equipment).filter_by(archived=False)],
        "delay_reasons": [{"id": d.id, "category": d.category, "reason": d.reason,
                           "stage": d.stage or "", "department": d.department}
                          for d in db.query(M.DelayReason).filter_by(archived=False)],
        "heat_series": [{"id": s.id, "prefix": s.prefix, "next_number": s.next_number,
                         "pad": s.pad, "active": s.active}
                        for s in db.query(M.HeatSeries)],
    }


class GradeIn(BaseModel):
    code: str
    name: str = ""
    specs: dict  # {element: {min, aim, max}}


@router.post("/grades")
def add_grade(body: GradeIn, user: M.User = Depends(require_perm("admin")),
              db: DBSession = Depends(get_db)):
    if db.query(M.SteelGrade).filter_by(code=body.code).first():
        raise HTTPException(400, "Grade code already exists")
    g = M.SteelGrade(code=body.code, name=body.name or body.code)
    db.add(g); db.flush()
    for el, s in body.specs.items():
        db.add(M.GradeSpec(grade_id=g.id, element=el, min_pct=s.get("min", 0),
                           aim_pct=s.get("aim", 0), max_pct=s.get("max", 0)))
    audit(db, user, "admin", "grade_created", detail=body.code)
    db.commit(); return {"ok": True, "id": g.id}


class GradeSpecsIn(BaseModel):
    specs: dict          # {element: {min, aim, max}} — the full set for the grade
    name: str | None = None


@router.put("/grades/{grade_id}/specs")
def set_grade_specs(grade_id: int, body: GradeSpecsIn,
                    user: M.User = Depends(require_perm("admin")),
                    db: DBSession = Depends(get_db)):
    """Set (or revise) a grade's target chemistry.

    Specs could only be given at creation, so any grade that arrived another
    way — auto-registered at heat entry, or imported with the plant's history —
    had no window for the validation engine to check chemistry against, and no
    aim for the calculator to work toward, with no way to supply one.

    An element sent with all three values zero is treated as "not specified"
    and its row is removed, so a spec can be cleared as well as corrected.
    """
    g = db.get(M.SteelGrade, grade_id)
    if not g:
        raise HTTPException(404, "Grade not found")
    before = {s.element: {"min": s.min_pct, "aim": s.aim_pct, "max": s.max_pct}
              for s in g.specs}
    existing = {s.element: s for s in g.specs}
    kept = set()
    for el, v in (body.specs or {}).items():
        el = str(el).strip()
        if not el:
            continue
        mn, aim, mx = (float(v.get("min") or 0), float(v.get("aim") or 0),
                       float(v.get("max") or 0))
        if mn == aim == mx == 0:
            continue                      # blank row — nothing specified
        if mx and mn and mn > mx:
            raise HTTPException(400, f"{el}: minimum {mn} is above maximum {mx}.")
        if aim and mn and mx and not (mn <= aim <= mx):
            raise HTTPException(400, f"{el}: aim {aim} is outside its own {mn}–{mx} window.")
        kept.add(el)
        row = existing.get(el)
        if row:
            row.min_pct, row.aim_pct, row.max_pct = mn, aim, mx
        else:
            db.add(M.GradeSpec(grade_id=g.id, element=el,
                               min_pct=mn, aim_pct=aim, max_pct=mx))
    for el, row in existing.items():       # elements the admin cleared
        if el not in kept:
            db.delete(row)
    if body.name:
        g.name = body.name.strip()
    # Grade specs decide which heats are flagged off-spec, so a change to them
    # is a change to the validation rules: record what it was, not just that it
    # happened.
    audit(db, user, "admin", "grade_specs_updated", detail=(
        f"{g.code}: {before or 'no specs'} -> "
        f"{ {el: v for el, v in (body.specs or {}).items() if el in kept} }")[:900])
    db.commit()
    return {"ok": True, "code": g.code, "elements": sorted(kept)}


class MaterialIn(BaseModel):
    code: str
    name: str
    category: str
    unit: str = "kg"
    composition: dict = {}


@router.post("/materials")
def add_material(body: MaterialIn, user: M.User = Depends(require_perm("admin")),
                 db: DBSession = Depends(get_db)):
    if db.query(M.Material).filter_by(code=body.code).first():
        raise HTTPException(400, "Material code already exists")
    m = M.Material(code=body.code, name=body.name, category=body.category,
                   unit=body.unit, composition=body.composition)
    db.add(m)
    audit(db, user, "admin", "material_created", detail=body.code)
    db.commit(); return {"ok": True, "id": m.id}


class DelayReasonIn(BaseModel):
    category: str            # Agency
    reason: str
    stage: str = ""          # eaf|zpf|lrf|vd|caster|"" (all stages)
    department: str = ""


@router.post("/delay-reasons")
def add_delay_reason(body: DelayReasonIn,
                     user: M.User = Depends(require_perm("admin")),
                     db: DBSession = Depends(get_db)):
    db.add(M.DelayReason(category=body.category, reason=body.reason,
                         stage=body.stage, department=body.department or body.category))
    audit(db, user, "admin", "delay_reason_created", detail=body.reason)
    db.commit(); return {"ok": True}


class SeriesIn(BaseModel):
    prefix: str
    next_number: int
    pad: int = 6


@router.post("/heat-series")
def set_series(body: SeriesIn, user: M.User = Depends(require_perm("admin")),
               db: DBSession = Depends(get_db)):
    for s in db.query(M.HeatSeries):
        s.active = False
    db.add(M.HeatSeries(prefix=body.prefix, next_number=body.next_number,
                        pad=body.pad, active=True))
    audit(db, user, "admin", "heat_series_changed",
          detail=f"{body.prefix}{body.next_number}")
    db.commit(); return {"ok": True}


# ─────────── Audit / Settings / Backup ───────────

@router.get("/audit")
def audit_log(category: str | None = None, heat_no: str | None = None,
              limit: int = 200, user: M.User = Depends(require_perm("admin")),
              db: DBSession = Depends(get_db)):
    q = db.query(M.AuditLog).order_by(M.AuditLog.at.desc())
    if category:
        q = q.filter(M.AuditLog.category == category)
    if heat_no:
        q = q.filter(M.AuditLog.heat_no == heat_no.upper())
    return [{"at": a.at.isoformat(sep=" "), "user": a.user_name,
             "category": a.category, "action": a.action,
             "heat_no": a.heat_no, "detail": a.detail}
            for a in q.limit(limit)]


@router.get("/settings")
def settings(user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    return {s.key: s.value for s in db.query(M.SystemSetting)}


class SettingIn(BaseModel):
    key: str
    value: str
    reauth_password: str


@router.post("/settings")
def set_setting(body: SettingIn, user: M.User = Depends(require_perm("admin")),
                db: DBSession = Depends(get_db)):
    _reauth(db, user, body.reauth_password)
    s = db.get(M.SystemSetting, body.key) or M.SystemSetting(key=body.key)
    s.value = body.value
    db.merge(s)
    audit(db, user, "admin", "setting_changed", detail=f"{body.key}={body.value}")
    db.commit(); return {"ok": True}


@router.post("/backup")
def backup(user: M.User = Depends(require_perm("admin")), db: DBSession = Depends(get_db)):
    """Business Continuity (11.12): on-demand snapshot.

    Was `shutil.copy2` of the .db file, which in WAL mode silently omitted
    transactions still in the write-ahead log — the same fault found in
    deploy/backup.sh, where a restore test recovered 13 of 16 heats. Both now
    go through the one engine, which uses SQLite's online backup API.
    """
    r = BK.create_snapshot(db, reason=f"manual · {user.name}")
    if not r.get("ok"):
        raise HTTPException(400, r.get("error", "Backup failed"))
    BK.prune()
    audit(db, user, "admin", "backup_created", detail=r["filename"])
    L.notify(db, "Database backup created",
             f"{r['filename']} · by {user.name}"
             + (" · encrypted" if r.get("encrypted") else ""), kind="system")
    db.commit()
    return r


@router.get("/backup/list")
def backups(user: M.User = Depends(require_perm("admin"))):
    return [{"filename": s["filename"], "size_kb": round(s["bytes"] / 1024, 1),
             "at": s["at"], "encrypted": s["encrypted"]}
            for s in BK.list_snapshots()]


@router.get("/backup/status")
def backup_status(user: M.User = Depends(require_perm("admin")),
                  db: DBSession = Depends(get_db)):
    """Drives the 7-day 'save a copy off the server' reminder and the
    failsafe banner. Read-only — it never restores anything by itself."""
    return {"reminder": BK.download_reminder(db),
            "recovery": BK.recovery_check(db),
            "auto_every_days": BK.AUTO_BACKUP_EVERY_DAYS,
            "encryption_on": bool(os.environ.get("SMS2_BACKUP_PASSPHRASE"))}


@router.post("/backup/restore")
async def restore_backup(file: UploadFile = File(...),
                         reauth_password: str = Form(...),
                         passphrase: str = Form(""),
                         user: M.User = Depends(require_perm("admin")),
                         db: DBSession = Depends(get_db)):
    """Replace the live database from an uploaded snapshot.

    This is the most destructive operation in SMS-2 — it discards every record
    created since the snapshot — so it carries the strictest gate in the
    system:

      · Administrator-class only (`require_perm("admin")`). It is deliberately
        NOT available to operators or engineers: a full restore can erase
        locked heats and rewrite the audit trail, which no production role
        should be able to do.
      · Password re-authentication, like every other critical operation.
      · The upload is VALIDATED as a real SMS-2 database before anything is
        touched — right tables, readable, integrity check passes.
      · The current database is snapshotted first, so a mistaken restore is
        itself reversible.

    Provided because on a hosted deployment there is no shell: Admin → Backup
    is the only recovery path. On a plant server prefer deploy/restore.sh with
    the service stopped.
    """
    _reauth(db, user, reauth_password)
    src = BK.sqlite_path()
    if not src:
        raise HTTPException(400, "PostgreSQL deployment — restore with psql on the server.")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file.")

    tmp_dir = os.path.join(DATA_DIR, "_restore_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    staged = os.path.join(tmp_dir, "upload.bin")
    with open(staged, "wb") as fh:
        fh.write(raw)

    try:
        candidate = staged
        # Encrypted snapshot: decrypt with the supplied passphrase.
        if file.filename.endswith(".enc") or raw[:8] == b"Salted__":
            if not passphrase:
                raise HTTPException(400, "That backup is encrypted — enter its passphrase.")
            if not shutil.which("openssl"):
                raise HTTPException(400, "openssl is not available on the server to decrypt.")
            dec = os.path.join(tmp_dir, "decrypted.db")
            import subprocess
            proc = subprocess.run(
                ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                 "-pass", "env:SMS2_RESTORE_PASS", "-in", staged, "-out", dec],
                capture_output=True, env={**os.environ, "SMS2_RESTORE_PASS": passphrase})
            if proc.returncode != 0:
                raise HTTPException(400, "Could not decrypt — wrong passphrase, or the file is damaged. Nothing was changed.")
            candidate = dec

        # Validate BEFORE touching the live database.
        try:
            c = sqlite3.connect(candidate)
            if c.execute("PRAGMA integrity_check;").fetchone()[0] != "ok":
                raise ValueError("integrity check failed")
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table';")}
            missing = {"heats", "users", "roles", "audit_log"} - tables
            if missing:
                raise ValueError(f"not an SMS-2 database (missing {', '.join(sorted(missing))})")
            heats = c.execute("SELECT COUNT(*) FROM heats;").fetchone()[0]
            users_n = c.execute("SELECT COUNT(*) FROM users;").fetchone()[0]
            c.close()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Rejected: {e}. Nothing was changed.")

        # Snapshot the CURRENT database so this restore is reversible.
        safety = BK.create_snapshot(db, reason=f"pre-restore · {user.name}")

        # Release pooled connections, then copy the validated image in.
        from ..core.database import engine as _engine
        _engine.dispose()
        s = sqlite3.connect(candidate)
        d = sqlite3.connect(src)
        with d:
            s.backup(d)
        d.close()
        s.close()
        for side in ("-wal", "-shm"):
            try:
                os.remove(src + side)
            except OSError:
                pass

        audit(db, user, "admin", "database_restored",
              detail=f"{file.filename} · {heats} heats, {users_n} users restored")
        db.commit()
        return {"ok": True, "heats": heats, "users": users_n,
                "safety_copy": safety.get("filename"),
                "note": "Sign out and back in — your session predates the restored data."}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/backup/{fname}/download")
def download_backup(fname: str, user: M.User = Depends(require_perm("admin")),
                    db: DBSession = Depends(get_db)):
    path = os.path.join(BACKUP_DIR, os.path.basename(fname))
    if not os.path.exists(path):
        raise HTTPException(404, "Backup not found")
    # Taking a copy off the server is what resets the 7-day reminder.
    BK.mark_downloaded(db)
    audit(db, user, "admin", "backup_downloaded", detail=os.path.basename(path))
    db.commit()
    return FileResponse(path, filename=os.path.basename(path))


# ═══ OWNERSHIP TRANSFER + ROLE HIERARCHY OPERATIONS (Constitution S.2) ═══

class TransferIn(BaseModel):
    target_user_id: int
    reauth_password: str


@router.post("/transfer-ownership")
def transfer_ownership(body: TransferIn, user: M.User = Depends(get_current_user),
                       db: DBSession = Depends(get_db)):
    """Ownership transfer: current Owner (or a Super Administrator when no
    active Owner exists) hands the Owner role to another active user; the
    previous Owner becomes Super Administrator. Fully audited."""
    if user.role.name not in ("Owner", "Super Administrator"):
        raise HTTPException(403, "Ownership transfer requires the Owner or a Super Administrator.")
    _reauth(db, user, body.reauth_password)
    target = db.get(M.User, body.target_user_id)
    if not target or not target.active or target.archived:
        raise HTTPException(400, "Target must be an existing ACTIVE user.")
    owner_role = db.query(M.Role).filter_by(name="Owner").first()
    super_role = db.query(M.Role).filter_by(name="Super Administrator").first()
    current_owner = (db.query(M.User).join(M.Role)
                     .filter(M.Role.name == "Owner", M.User.active == True,   # noqa
                             M.User.archived == False).first())               # noqa
    if user.role.name == "Super Administrator" and current_owner:
        raise HTTPException(403, "An active Owner exists — only the Owner may transfer ownership.")
    if current_owner and current_owner.id != target.id:
        current_owner.role_id = super_role.id
    # Business continuity: promoting the target must not empty a protected
    # class (e.g. the last active Shift Engineer cannot become Owner).
    _continuity_check(db, target, "Owner", deactivating=False)
    target.role_id = owner_role.id
    audit(db, user, "admin", "ownership_transferred",
          detail=f"Owner → {target.employee_id} ({target.name})"
                 + (f"; previous owner {current_owner.employee_id} → Super Administrator"
                    if current_owner else ""))
    db.commit()
    return {"ok": True, "new_owner": target.employee_id}


class EquipStatusIn(BaseModel):
    status: str      # available | maintenance | offline
    reauth_password: str


@router.post("/equipment/{code}/status")
def set_equipment_status(code: str, body: EquipStatusIn,
                         user: M.User = Depends(require_perm("admin")),
                         db: DBSession = Depends(get_db)):
    """Machine states (Constitution S.5): maintenance / offline are declared
    by governance; running / delayed / idle are computed live."""
    if body.status not in ("available", "maintenance", "offline"):
        raise HTTPException(400, "Status must be available, maintenance or offline.")
    _reauth(db, user, body.reauth_password)
    eq = db.query(M.Equipment).filter_by(code=code.upper()).first()
    if not eq:
        raise HTTPException(404, "Equipment not found")
    eq.status = body.status
    audit(db, user, "admin", "equipment_status", detail=f"{eq.code} → {body.status}")
    db.commit()
    return {"ok": True}


# ═══ ADMIN APPROVAL LAW — password recovery centre ═══

@router.get("/password-requests")
def list_password_requests(status: str = "pending",
                           user: M.User = Depends(require_perm("admin")),
                           db: DBSession = Depends(get_db)):
    q = db.query(M.PasswordResetRequest)
    if status:
        q = q.filter(M.PasswordResetRequest.status == status)
    out = []
    for r in q.order_by(M.PasswordResetRequest.requested_at.desc()).limit(200):
        u = db.query(M.User).filter_by(employee_id=r.employee_id).first()
        out.append({"id": r.id, "employee_id": r.employee_id, "user_name": r.user_name,
                    "remarks": r.remarks, "status": r.status, "action": r.action,
                    "requested_at": r.requested_at.isoformat(sep=" "),
                    "decided_by": r.decided_by_name,
                    "decided_at": r.decided_at and r.decided_at.isoformat(sep=" "),
                    "decision_remarks": r.decision_remarks,
                    "role": u.role.name if u and u.role else "—",
                    "account_status": ("archived" if u and u.archived else
                                       "active" if u and u.active else
                                       "disabled" if u else "unknown")})
    return out


class PwDecideIn(BaseModel):
    action: str                     # approve_reset | reject | disable_account
    remarks: str = ""
    temp_password: str | None = None    # optional; system generates when omitted
    reauth_password: str


@router.post("/password-requests/{rid}/decide")
def decide_password_request(rid: int, body: PwDecideIn,
                            user: M.User = Depends(require_perm("admin")),
                            db: DBSession = Depends(get_db)):
    """Approve → issue a new password, update the ledger, audit, notify.
    Reject / disable are equally first-class, audited outcomes."""
    _reauth(db, user, body.reauth_password)
    r = db.get(M.PasswordResetRequest, rid)
    if not r:
        raise HTTPException(404, "Request not found")
    if r.status != "pending":
        raise HTTPException(400, f"Request already {r.status}.")
    u = db.query(M.User).filter_by(employee_id=r.employee_id).first()
    if not u:
        raise HTTPException(404, "User account no longer exists")
    from ..engines.credentials import record, generate_password
    from ..core.security import hash_password, password_policy_errors
    r.decided_by, r.decided_by_name = user.id, user.name
    r.decided_at = plant_time.now_moment().at
    r.decision_remarks = body.remarks
    issued = None

    if body.action == "approve_reset":
        pw = (body.temp_password or "").strip() or generate_password()
        errs = password_policy_errors(pw)
        if errs:
            raise HTTPException(400, " ".join(errs))
        u.password_hash = hash_password(pw)
        u.active = True
        r.status, r.action = "approved", "password_reset"
        issued = pw
        # existing sessions must not survive a credential reset
        for s in db.query(M.Session).filter_by(user_id=u.id, revoked=False):
            s.revoked = True
        audit(db, user, "admin", "password_reset_approved", detail=u.employee_id)
        L.notify(db, f"Password reset approved — {u.employee_id}",
                 f"New credentials issued by {user.name}. Collect them from the "
                 f"administrator and change the password after signing in.",
                 kind="admin", priority="high", audience_role="Administrator")
        record(db, u.employee_id, pw)
    elif body.action == "disable_account":
        _continuity_check(db, u, None, deactivating=True)
        u.active = False
        r.status, r.action = "approved", "account_disabled"
        audit(db, user, "admin", "account_disabled_via_request", detail=u.employee_id)
        L.notify(db, f"Account disabled — {u.employee_id}",
                 f"Disabled by {user.name} following a recovery request.",
                 kind="admin", priority="high", audience_role="Administrator")
        record(db, u.employee_id, None)
    elif body.action == "reject":
        r.status, r.action = "rejected", "rejected"
        audit(db, user, "admin", "password_reset_rejected", detail=u.employee_id)
    else:
        raise HTTPException(400, "action must be approve_reset, reject or disable_account")
    db.commit()
    return {"ok": True, "status": r.status, "action": r.action,
            "temporary_password": issued}


class CredFileIn(BaseModel):
    reauth_password: str


@router.post("/credentials-file")
def credentials_file(body: CredFileIn,
                     user: M.User = Depends(require_perm("admin")),
                     db: DBSession = Depends(get_db)):
    """Administrator-only view of the credentials ledger (re-authenticated)."""
    _reauth(db, user, body.reauth_password)
    from ..engines.credentials import rewrite, CRED_PATH
    path = rewrite(db)
    audit(db, user, "admin", "credentials_file_viewed", detail=os.path.basename(path))
    db.commit()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {"path": path, "content": f.read()}
    except Exception as e:
        raise HTTPException(500, f"Ledger unavailable: {e}")
