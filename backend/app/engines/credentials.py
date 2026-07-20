"""
SMS-2 IDOS — CREDENTIALS LEDGER (User Management Law)
======================================================
Maintains `credentials.txt` at the project root, automatically rewritten
whenever the system creates a user, resets a password, enables/disables an
account, or changes a role.

SECURITY POSTURE (Industrial Security Law):
  * Only ISSUED credentials are recorded — the default password given at
    account creation and any admin-issued temporary password. The moment a
    user sets their own password the entry becomes "(user-managed)", so a
    user's private secret is never written to disk.
  * The file lives beside the database; server console access is the trust
    boundary. Over HTTP it is reachable only by Administrator-class roles
    with password re-authentication (see /api/admin/credentials-file).
"""
from __future__ import annotations
import os
import re
import secrets
import string
from datetime import datetime
from sqlalchemy.orm import Session
from .. import models as M

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CRED_PATH = os.path.join(ROOT, "credentials.txt")
USER_MANAGED = "(user-managed)"

HEADER = """\
################################################################################
                       SMS-2 IDOS — CREDENTIALS LEDGER
################################################################################
 Automatically maintained by SMS-2. Do not edit by hand.
 Updated on: user creation · password reset · enable · disable · role change.

 CONFIDENTIAL — Administrator access only.
 Only ISSUED credentials appear here (defaults and admin-issued temporary
 passwords). Once a user changes their own password the entry shows
 "{um}" — private passwords are never written to disk.
 Passwords are stored in the database only as PBKDF2-HMAC-SHA256 hashes.
################################################################################

""".format(um=USER_MANAGED)

COLS = ("USERNAME", "ISSUED PASSWORD", "ROLE", "STATUS", "CREATED", "LAST UPDATED")
W = (14, 22, 24, 12, 20, 20)


def generate_password(n: int = 12) -> str:
    """Policy-compliant temporary password (letters + digits, unambiguous)."""
    alpha = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    digits = "23456789"
    pool = alpha + digits
    while True:
        p = "".join(secrets.choice(pool) for _ in range(n))
        if any(c.isalpha() for c in p) and any(c.isdigit() for c in p):
            return p


def _read_known() -> dict:
    """Parse the existing ledger so previously issued passwords survive."""
    known = {}
    if not os.path.exists(CRED_PATH):
        return known
    try:
        with open(CRED_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.startswith("| "):
                    continue
                parts = [p.strip() for p in line.strip().strip("|").split("|")]
                if len(parts) >= 6 and parts[0] not in ("USERNAME", ""):
                    known[parts[0]] = {"password": parts[1], "created": parts[4],
                                       "updated": parts[5]}
    except Exception:
        pass
    return known


def _row(vals) -> str:
    return "| " + " | ".join(str(v)[:w].ljust(w) for v, w in zip(vals, W)) + " |"


def rewrite(db: Session, issued: dict | None = None):
    """Rewrite the ledger from the live user table.
    `issued` = {employee_id: password_or_USER_MANAGED} for this event."""
    known = _read_known()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if issued:
        for emp, pw in issued.items():
            known[emp] = {**known.get(emp, {}), "password": pw, "updated": stamp}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [HEADER, _row(COLS), "|" + "|".join("-" * (w + 2) for w in W) + "|"]
    for u in (db.query(M.User).order_by(M.User.employee_id)):
        k = known.get(u.employee_id, {})
        pw = k.get("password") or ("(unknown — issued before ledger)" if not issued
                                   or u.employee_id not in issued else "")
        status = "ARCHIVED" if u.archived else ("ACTIVE" if u.active else "DISABLED")
        created = k.get("created") or (u.created_at.strftime("%Y-%m-%d %H:%M")
                                       if getattr(u, "created_at", None) else "—")
        updated = k.get("updated") or "—"
        lines.append(_row((u.employee_id, pw, u.role.name if u.role else "—",
                           status, created, updated)))
    lines.append("")
    lines.append(f"Ledger written {now} · {db.query(M.User).count()} account(s)")
    lines.append("")
    tmp = CRED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, CRED_PATH)          # atomic — the ledger can never be half-written
    try:
        os.chmod(CRED_PATH, 0o600)      # owner-only on POSIX
    except Exception:
        pass
    return CRED_PATH


def record(db: Session, employee_id: str, password: str | None):
    """Convenience: record one credential event and rewrite the ledger."""
    return rewrite(db, {employee_id: password if password else USER_MANAGED})
