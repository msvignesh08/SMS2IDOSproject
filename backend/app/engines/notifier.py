"""Optional email delivery for critical notifications (Preference Engine).
REAL implementation, gated on plant SMTP configuration — set the
`smtp_host` (and optionally smtp_port, smtp_from) system settings in
Admin → Settings to activate. Delivery runs in a daemon thread and can
never block or fail production (Constitution S.7)."""
from __future__ import annotations
import smtplib
import threading
from email.message import EmailMessage
from sqlalchemy.orm import Session
from .. import models as M


def _setting(db: Session, key: str, default: str = "") -> str:
    s = db.query(M.SystemSetting).filter_by(key=key).first()
    return (s.value if s else default) or default


def email_critical(db: Session, title: str, body: str):
    """Send `title/body` to users who opted in to email (or haven't set
    prefs yet but have an address) — only when SMTP is configured."""
    host = _setting(db, "smtp_host")
    if not host:
        return                      # not configured — in-app notifications carry it
    port = int(_setting(db, "smtp_port", "25") or 25)
    sender = _setting(db, "smtp_from", "sms2-idos@plant.local")
    rcpts = []
    for u in db.query(M.User).filter(M.User.active == True,       # noqa: E712
                                     M.User.archived == False):   # noqa: E712
        if not u.email:
            continue
        prefs = u.notification_prefs
        if prefs is None or prefs.get("email", True):
            rcpts.append(u.email)
    if not rcpts:
        return

    def _send():
        try:
            msg = EmailMessage()
            msg["Subject"] = f"[SMS-2 CRITICAL] {title}"
            msg["From"] = sender
            msg["To"] = ", ".join(rcpts)
            msg.set_content(body or title)
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.send_message(msg)
        except Exception:
            pass                    # email failure must never affect production

    threading.Thread(target=_send, daemon=True).start()
