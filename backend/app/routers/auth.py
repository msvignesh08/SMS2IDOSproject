"""Auth: employee-ID login, DB-backed sessions (intranet, no cloud), RBAC deps."""
from __future__ import annotations
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession
from ..core.database import get_db
from ..core.security import verify_password, new_session_token, hash_password, password_policy_errors
from ..core.config import SESSION_HOURS
from ..core import plant_time
from .. import models as M
from ..engines.lifecycle import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_current_user(authorization: str | None = Header(None),
                     db: DBSession = Depends(get_db)) -> M.User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.removeprefix("Bearer ").strip()
    s = db.query(M.Session).filter_by(token=token, revoked=False).first()
    if not s or s.expires_at < plant_time.plant_now():
        raise HTTPException(401, "Session expired — please sign in again")
    if not s.user.active or s.user.archived:
        raise HTTPException(403, "Account is deactivated")
    return s.user


def require_perm(perm: str):
    def dep(user: M.User = Depends(get_current_user)) -> M.User:
        perms = user.role.permissions or []
        if perm not in perms and "admin" not in perms:
            raise HTTPException(403, f"Your role ({user.role.name}) lacks permission: {perm}")
        return user
    return dep


class LoginIn(BaseModel):
    employee_id: str
    password: str


@router.post("/login")
def login(body: LoginIn, db: DBSession = Depends(get_db)):
    u = db.query(M.User).filter(M.User.employee_id == body.employee_id.strip().upper()).first()
    if not u:
        audit(db, None, "auth", "login_failed", detail=f"{body.employee_id} (no such account)")
        db.commit()
        raise HTTPException(401, "Account not found. Please contact the Administrator.")
    if not u.active or u.archived:
        audit(db, None, "auth", "login_failed", detail=f"{u.employee_id} (account disabled)")
        db.commit()
        raise HTTPException(403, "This account has been disabled. Please contact the Administrator.")
    if not verify_password(body.password, u.password_hash):
        audit(db, None, "auth", "login_failed", detail=f"{u.employee_id} (wrong password)")
        db.commit()
        raise HTTPException(401, "Incorrect password. Please try again, or use \u2018Forgot password?\u2019 below.")
    token = new_session_token()
    db.add(M.Session(token=token, user_id=u.id,
                     expires_at=plant_time.plant_now() + timedelta(hours=SESSION_HOURS)))
    audit(db, u, "auth", "login")
    db.commit()
    m = plant_time.now_moment()
    return {"token": token,
            "user": {"id": u.id, "employee_id": u.employee_id, "name": u.name,
                     "role": u.role.name, "designation": u.designation,
                     "department": u.department, "theme": u.theme,
                     "permissions": u.role.permissions},
            "plant": {"plant_day": m.plant_day.isoformat(), "shift": m.shift,
                      "now": m.at.isoformat(sep=" ")},
            # Shown from the first screen, so someone signing in during a
            # maintenance window learns it before they start entering a heat.
            "maintenance": maintenance_state(db)}


@router.post("/logout")
def logout(user: M.User = Depends(get_current_user),
           authorization: str = Header(None), db: DBSession = Depends(get_db)):
    token = authorization.removeprefix("Bearer ").strip()
    s = db.query(M.Session).filter_by(token=token).first()
    if s:
        s.revoked = True
    audit(db, user, "auth", "logout")
    db.commit()
    return {"ok": True}


class PwIn(BaseModel):
    current: str
    new: str


@router.post("/password")
def change_password(body: PwIn, user: M.User = Depends(get_current_user),
                    db: DBSession = Depends(get_db)):
    if not verify_password(body.current, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    errs = password_policy_errors(body.new)
    if errs:
        raise HTTPException(400, " ".join(errs))
    db.merge(user)
    user.password_hash = hash_password(body.new)
    audit(db, user, "auth", "password_changed")
    db.commit()
    # User Management Law: the ledger records that this secret is now private
    try:
        from ..engines.credentials import record, USER_MANAGED
        record(db, user.employee_id, None)
    except Exception:
        pass
    return {"ok": True}


class ThemeIn(BaseModel):
    theme: str


@router.post("/theme")
def set_theme(body: ThemeIn, user: M.User = Depends(get_current_user),
              db: DBSession = Depends(get_db)):
    # "signature" is the fifth theme (the showcase mode). This is an allowlist
    # extension only — the endpoint's logic, storage and validation shape are
    # unchanged. Without it the theme applies in-session but silently fails to
    # persist, because the client swallows the 400.
    if body.theme not in ("light", "dark", "black", "lowpower", "signature"):
        raise HTTPException(400, "Theme must be light, dark, black, lowpower or signature")
    db.merge(user)
    user.theme = body.theme
    db.commit()
    return {"ok": True}


def maintenance_state(db: DBSession) -> dict:
    """Is the plant in maintenance, and what should users be told?

    Read here rather than in a middleware because maintenance in SMS-2 WARNS,
    it does not lock: an operator mid-heat must always be able to finish
    recording it, or the shift loses data the paper log would have kept.
    """
    rows = {s.key: (s.value or "") for s in db.query(M.SystemSetting)
            if s.key in ("maintenance_mode", "maintenance_message")}
    on = rows.get("maintenance_mode", "off").strip().lower() in ("on", "1", "true", "yes")
    return {"on": on, "message": rows.get("maintenance_message", "").strip()}


@router.get("/me")
def me(user: M.User = Depends(get_current_user), db: DBSession = Depends(get_db)):
    m = plant_time.now_moment()
    return {"user": {"id": user.id, "employee_id": user.employee_id,
                     "name": user.name, "role": user.role.name,
                     "theme": user.theme, "permissions": user.role.permissions,
                     "email": user.email or "",
                     "notification_prefs": user.notification_prefs},
            "plant": {"plant_day": m.plant_day.isoformat(), "shift": m.shift,
                      "shift_start": m.shift_start.isoformat(sep=" "),
                      "shift_end": m.shift_end.isoformat(sep=" "),
                      "now": m.at.isoformat(sep=" ")},
            "maintenance": maintenance_state(db)}


# ═══ NOTIFICATION PREFERENCE ENGINE ═══
# Optional kinds are user-controlled; MANDATORY kinds can never be disabled:
# emergency, security, system (incl. database failure), delay (production
# delays / heat failures), and anything with critical priority.
OPTIONAL_NOTIFY_KINDS = ["production", "approval", "ai", "report", "admin",
                         "shift", "sync", "autosave"]
NOTIFY_CHANNELS = ["browser", "email"]


class PrefsIn(BaseModel):
    prefs: dict


@router.post("/notification-prefs")
def save_notification_prefs(body: PrefsIn,
                            user: M.User = Depends(get_current_user),
                            db: DBSession = Depends(get_db)):
    clean = {}
    for k in OPTIONAL_NOTIFY_KINDS + NOTIFY_CHANNELS:
        if k in (body.prefs or {}):
            clean[k] = bool(body.prefs[k])
    # THE SOUND NOTIFICATION LAW — user-controlled sound policy
    mode = (body.prefs or {}).get("sound_mode")
    if mode in ("important", "approval", "critical", "silent"):
        clean["sound_mode"] = mode
    vol = (body.prefs or {}).get("sound_volume")
    if vol is not None:
        try:
            clean["sound_volume"] = max(0.0, min(1.0, float(vol)))
        except (TypeError, ValueError):
            pass
    email = (body.prefs or {}).get("email_address")
    if email is not None:
        user.email = str(email).strip()
    user.notification_prefs = clean
    audit(db, user, "auth", "notification_prefs_saved",
          detail=", ".join(f"{k}={'on' if v else 'off'}" for k, v in clean.items()))
    db.commit()
    return {"ok": True, "prefs": clean}


# ═══ FORGOT PASSWORD LAW — request, never auto-reset ═══

class ForgotIn(BaseModel):
    employee_id: str
    remarks: str = ""


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, db: DBSession = Depends(get_db)):
    """Unauthenticated recovery REQUEST. The system never resets a password
    by itself and never reveals whether an account exists."""
    emp = (body.employee_id or "").strip().upper()
    if emp:
        u = db.query(M.User).filter(M.User.employee_id == emp).first()
        if u:
            open_req = (db.query(M.PasswordResetRequest)
                        .filter_by(employee_id=emp, status="pending").first())
            if not open_req:
                db.add(M.PasswordResetRequest(employee_id=emp, user_name=u.name,
                                              remarks=(body.remarks or "").strip()[:500]))
                db.add(M.AuditLog(user_id=None, user_name=emp, category="auth",
                                  action="password_reset_requested",
                                  detail=(body.remarks or "")[:200]))
                db.add(M.Notification(
                    kind="approval", priority="high",
                    title=f"Password recovery request — {emp}",
                    body=f"{u.name} ({u.role.name if u.role else '—'}) requested a password reset."
                         + (f" Remarks: {body.remarks.strip()[:200]}" if body.remarks.strip() else ""),
                    audience_role="Administrator"))
                db.commit()
    return {"ok": True, "message": "If the employee ID exists, a recovery request has been "
                                   "sent to the plant administrator for approval. "
                                   "You will be contacted with new credentials."}
