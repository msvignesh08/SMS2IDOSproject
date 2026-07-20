"""
SMS-2 IDOS — BACKUP & RECOVERY ENGINE (Business Continuity, 11.12)

One place that knows how to make a backup, so the shell script, the admin
button and the automatic schedule cannot drift apart.

Two things this module exists to get right
------------------------------------------
1. **WAL-safe snapshots.** The database runs in WAL mode: recent transactions
   live in `sms2_idos.db-wal` and are not yet in the main file. Copying the
   file with `shutil.copy2` silently omits them — a restore test recovered 13
   of 16 heats. Every snapshot here uses SQLite's online backup API, which
   copies a transactionally consistent image including the WAL, and is safe
   while the server is serving.

2. **Encryption at rest.** A backup contains every password hash and the whole
   audit trail. If `SMS2_BACKUP_PASSPHRASE` is set, the snapshot is encrypted
   with AES-256 (PBKDF2, 200k iterations) via openssl, matching
   deploy/backup.sh so the two are interchangeable.

Schedules are recorded in `system_settings` so they survive a restart.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .. import models as M
from ..core.config import BACKUP_DIR, DATABASE_URL

# How often the server takes its own snapshot, and how often it asks a human
# to take a copy away with them. Both are deliberately conservative: a backup
# that only ever exists on the same disk as the database is not a backup.
AUTO_BACKUP_EVERY_DAYS = 3
REMIND_DOWNLOAD_EVERY_DAYS = 7

K_LAST_AUTO = "backup_last_auto"
K_LAST_DOWNLOAD = "backup_last_download"


# ───────────────────────────── settings helpers ─────────────────────────────
def _get(db: Session, key: str) -> str:
    s = db.query(M.SystemSetting).filter_by(key=key).first()
    return (s.value if s else "") or ""


def _set(db: Session, key: str, value: str, desc: str = ""):
    s = db.query(M.SystemSetting).filter_by(key=key).first()
    if s:
        s.value = value
    else:
        db.add(M.SystemSetting(key=key, value=value, description=desc))


def _days_since(stamp: str) -> float | None:
    if not stamp:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(stamp)).total_seconds() / 86400.0
    except ValueError:
        return None


# ───────────────────────────── snapshot ─────────────────────────────
def sqlite_path() -> str | None:
    return DATABASE_URL.replace("sqlite:///", "") if DATABASE_URL.startswith("sqlite") else None


def _encrypt(path: str) -> str:
    """AES-256 the snapshot if a passphrase is configured. Returns final path."""
    passphrase = os.environ.get("SMS2_BACKUP_PASSPHRASE")
    if not passphrase:
        return path
    if not shutil.which("openssl"):
        return path                      # documented in the API response
    out = path + ".enc"
    try:
        subprocess.run(
            ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt",
             "-pass", "env:SMS2_BACKUP_PASSPHRASE", "-in", path, "-out", out],
            check=True, capture_output=True,
            env={**os.environ, "SMS2_BACKUP_PASSPHRASE": passphrase})
    except (subprocess.CalledProcessError, OSError):
        return path                      # keep the plaintext rather than nothing
    os.remove(path)
    return out


def create_snapshot(db: Session, reason: str = "manual") -> dict:
    """Take a WAL-safe (and optionally encrypted) snapshot. Never raises."""
    src = sqlite_path()
    if not src:
        return {"ok": False, "error": "PostgreSQL deployment — use deploy/backup.sh (pg_dump)."}
    os.makedirs(BACKUP_DIR, exist_ok=True)
    fname = f"sms2_backup_{datetime.now():%Y%m%d_%H%M%S}.db"
    dst = os.path.join(BACKUP_DIR, fname)
    try:
        db.commit()                      # flush our own pending work first
        # THE important line: SQLite's online backup, not a file copy.
        s = sqlite3.connect(src)
        d = sqlite3.connect(dst)
        with d:
            s.backup(d)
        d.close()
        s.close()
        final = _encrypt(dst)
        _set(db, K_LAST_AUTO, datetime.now().isoformat(timespec="seconds"),
             "Last automatic database snapshot")
        db.commit()
        return {"ok": True, "filename": os.path.basename(final),
                "encrypted": final.endswith(".enc"), "reason": reason}
    except Exception as e:                # a failed backup must never stop production
        return {"ok": False, "error": str(e)[:200]}


def prune(keep: int = 60):
    """Keep the newest `keep` snapshots."""
    try:
        files = sorted((os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)),
                       key=os.path.getmtime, reverse=True)
        for f in files[keep:]:
            os.remove(f)
    except OSError:
        pass


def list_snapshots() -> list[dict]:
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        p = os.path.join(BACKUP_DIR, f)
        if os.path.isfile(p):
            st = os.stat(p)
            out.append({"filename": f, "bytes": st.st_size,
                        "at": datetime.fromtimestamp(st.st_mtime).isoformat(sep=" ", timespec="seconds"),
                        "encrypted": f.endswith(".enc")})
    return out


# ───────────────────────────── schedules ─────────────────────────────
def maybe_auto_backup(db: Session) -> dict | None:
    """Snapshot if the last one is older than AUTO_BACKUP_EVERY_DAYS."""
    age = _days_since(_get(db, K_LAST_AUTO))
    if age is not None and age < AUTO_BACKUP_EVERY_DAYS:
        return None
    r = create_snapshot(db, reason="scheduled")
    if r.get("ok"):
        prune()
    return r


def download_reminder(db: Session) -> dict:
    """Should an administrator be asked to take a copy off the server?

    A snapshot sitting in backend/data/backups is on the SAME DISK as the
    database it protects. Disk dies, both die. This is the nudge to keep an
    off-server copy — and on an ephemeral host it is the only copy that
    survives a restart at all.
    """
    age = _days_since(_get(db, K_LAST_DOWNLOAD))
    return {"due": age is None or age >= REMIND_DOWNLOAD_EVERY_DAYS,
            "days_since": None if age is None else round(age, 1),
            "every_days": REMIND_DOWNLOAD_EVERY_DAYS,
            "never": age is None}


def mark_downloaded(db: Session):
    _set(db, K_LAST_DOWNLOAD, datetime.now().isoformat(timespec="seconds"),
         "Last time an administrator saved a backup off the server")
    db.commit()


# ───────────────────────────── failsafe ─────────────────────────────
def recovery_check(db: Session) -> dict:
    """Does this look like a database that lost its data, with a way back?

    Deliberately only REPORTS. It never restores by itself: a silent automatic
    restore would overwrite a legitimately new plant on its first day, and
    would hide the fault that caused the loss. A human decides.
    """
    heats = db.query(M.Heat).count()
    snaps = list_snapshots()
    return {"heats": heats, "snapshots": len(snaps),
            "latest": snaps[0] if snaps else None,
            # Empty database + a snapshot on disk = almost certainly a wipe.
            "recoverable": heats == 0 and bool(snaps)}
