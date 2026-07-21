"""
SMS-2 IDOS v1.0 — application entry point.
Run:  uvicorn app.main:app --host 0.0.0.0 --port 8020   (from backend/)
Serves the API under /api and the SMS-2 web application at /.
"""
import os
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .core.database import Base, engine, SessionLocal
from .core.config import APP_NAME, APP_VERSION, APP_MOTTO
from . import models  # noqa: F401 — register models
from .seed import seed
from .routers import auth, heats, dashboard, reports, admin, ai

app = FastAPI(title=APP_NAME, version=APP_VERSION, description=APP_MOTTO)

app.include_router(auth.router)
app.include_router(heats.router)
app.include_router(dashboard.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(ai.router)


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed(db)
        # OPTIONAL HISTORY BACKFILL — set SMS2_SEED_HISTORY=1.
        # Managed hosts on their free tiers give no shell, so there is no way
        # to run tools/seed_history.py after a deploy. This lets the service
        # load the committed history bundle itself on first boot. Off by
        # default (local runs and demos stay empty), idempotent (heats already
        # present are skipped), and non-fatal for the same reason as the
        # backup block below: nothing may stop the plant coming up.
        if (os.environ.get("SMS2_SEED_HISTORY") or "").strip().lower() in ("1", "true", "yes"):
            try:
                import sys
                from . import models as M
                sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                from tools.history_writer import HistoryWriter
                from tools.seed_history import BUNDLE, load
                if db.query(M.Heat).filter_by(is_imported=True).count():
                    print("SMS-2: history already present — backfill skipped.")
                elif not BUNDLE.exists():
                    print("SMS-2: SMS2_SEED_HISTORY is set, but no bundle is "
                          "committed at app/data/history.json.gz.")
                else:
                    heats, meta = load(BUNDLE)
                    n = HistoryWriter(db).write(heats, meta.get("source") or "history bundle")
                    db.commit()
                    print(f"SMS-2: history backfill seeded {n} heats from {meta.get('source')}.")
            except Exception as e:
                db.rollback()
                print(f"SMS-2: history backfill failed (non-fatal): {e}")
        # Business continuity: take a snapshot if the last one is older than
        # the schedule. Wrapped because a failed backup must NEVER stop the
        # plant from coming up — production continuity outranks housekeeping.
        try:
            from .engines import backup as BK
            r = BK.maybe_auto_backup(db)
            if r and r.get("ok"):
                print(f"SMS-2: scheduled backup written — {r['filename']}"
                      + (" (encrypted)" if r.get("encrypted") else ""))
            chk = BK.recovery_check(db)
            if chk.get("recoverable"):
                # Reported, never acted on automatically: silently restoring
                # would overwrite a legitimately new plant on its first day
                # and hide whatever caused the loss. A human decides.
                print("SMS-2: database is EMPTY but "
                      f"{chk['snapshots']} backup(s) exist — recovery is offered "
                      "in Admin → Backup.")
        except Exception as e:
            print(f"SMS-2: backup subsystem unavailable (non-fatal): {e}")
    finally:
        db.close()


FRONTEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "frontend")

app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))


@app.get("/health")
def health():
    """Liveness probe, plus the PLANT CLOCK the server is actually running on.

    A deployment can be perfectly healthy and still keep the wrong time: read
    naively, a host in UTC reports 12:09 while the shop floor is at 17:39, and
    every heat is then filed against the wrong shift and plant day. Exposing
    the resolved plant day and shift here makes that visible without signing
    in, so a bad SMS2_PLANT_TZ is caught by looking rather than by discovering
    mis-filed heats a week later. Nothing here is sensitive — it is the wall
    clock and the configured zone, no production data.
    """
    from .core import plant_time as pt
    m = pt.now_moment()
    return {"app": APP_NAME, "version": APP_VERSION, "status": "ok",
            "motto": APP_MOTTO,
            "plant_day": m.plant_day.isoformat(), "shift": m.shift,
            "plant_time": m.at.isoformat(sep=" "),
            "plant_tz": pt._TZ_NAME or "server local time"}
