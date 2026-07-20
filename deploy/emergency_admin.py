#!/usr/bin/env python3
"""
SMS-2 IDOS — EMERGENCY ADMIN RECOVERY (Constitution S.2 / S.7)
===============================================================
"THE DEACTIVATION OF AN ADMIN SHALL NEVER RESULT IN TOTAL ADMIN LOSS,
 SYSTEM LOCKOUT, OR BUSINESS FAILURE."

Run this ON THE SERVER CONSOLE (physical/SSH access is the security
boundary — the same trust level as the database file itself). It can:

  1. Reactivate every archived/disabled Administrator-class account, or
  2. Create/reset a dedicated RECOVERY admin account,

and records the entire action in the permanent audit log.

Usage (from the project root):
    python3 deploy/emergency_admin.py --reactivate
    python3 deploy/emergency_admin.py --create-recovery
    python3 deploy/emergency_admin.py --reset-password ADMIN

This script never deletes anything (Universal No-Delete).
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.core.database import SessionLocal, engine            # noqa: E402
from app.core.security import hash_password, password_policy_errors  # noqa: E402
from app import models as M                                   # noqa: E402


def validate_password(pwd):
    errs = password_policy_errors(pwd)
    return (not errs, "; ".join(errs))

ADMIN_CLASS = ("Owner", "Super Administrator", "Administrator")


def audit(db, action, detail):
    db.add(M.AuditLog(user_id=None, user_name="EMERGENCY-CONSOLE",
                      category="admin", action=action, detail=detail))


def main():
    ap = argparse.ArgumentParser(description="SMS-2 emergency admin recovery")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--reactivate", action="store_true",
                   help="Reactivate all Administrator-class accounts")
    g.add_argument("--create-recovery", action="store_true",
                   help="Create (or reset) the RECOVERY Super Administrator")
    g.add_argument("--reset-password", metavar="EMPLOYEE_ID",
                   help="Reset the password of an existing user")
    args = ap.parse_args()

    M.Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if args.reactivate:
            n = 0
            for u in (db.query(M.User).join(M.Role)
                      .filter(M.Role.name.in_(ADMIN_CLASS))):
                if not u.active or u.archived:
                    u.active, u.archived = True, False
                    n += 1
                    print(f"  reactivated: {u.employee_id} ({u.name}, {u.role.name})")
            audit(db, "emergency_reactivate", f"{n} administrator account(s) reactivated")
            db.commit()
            print(f"DONE — {n} account(s) reactivated. Business continuity restored.")
            return

        if args.create_recovery:
            pwd = getpass.getpass("New password for RECOVERY account: ")
            ok, msg = validate_password(pwd)
            if not ok:
                sys.exit(f"REFUSED: {msg}")
            role = db.query(M.Role).filter_by(name="Super Administrator").first() \
                or db.query(M.Role).filter_by(name="Administrator").first()
            u = db.query(M.User).filter_by(employee_id="RECOVERY").first()
            if u:
                u.password_hash = hash_password(pwd)
                u.active, u.archived, u.role_id = True, False, role.id
                action = "reset"
            else:
                db.add(M.User(employee_id="RECOVERY", name="Emergency Recovery Admin",
                              designation="Recovery", department="IT",
                              role_id=role.id, password_hash=hash_password(pwd)))
                action = "created"
            audit(db, "emergency_recovery_admin", f"RECOVERY account {action}")
            db.commit()
            try:
                from app.engines.credentials import record
                record(db, "RECOVERY", pwd)
            except Exception:
                pass
            print(f"DONE — RECOVERY account {action}. Sign in as RECOVERY and "
                  "restore normal governance, then disable this account from Admin → Users.")
            return

        if args.reset_password:
            emp = args.reset_password.upper()
            u = db.query(M.User).filter_by(employee_id=emp).first()
            if not u:
                sys.exit(f"No user {emp}")
            pwd = getpass.getpass(f"New password for {emp}: ")
            ok, msg = validate_password(pwd)
            if not ok:
                sys.exit(f"REFUSED: {msg}")
            u.password_hash = hash_password(pwd)
            u.active = True
            audit(db, "emergency_password_reset", f"password reset for {emp}")
            db.commit()
            try:
                from app.engines.credentials import record
                record(db, emp, pwd)
            except Exception:
                pass
            print(f"DONE — password reset for {emp} (account active).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
