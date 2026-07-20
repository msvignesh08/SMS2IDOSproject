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
    return {"app": APP_NAME, "version": APP_VERSION, "status": "ok",
            "motto": APP_MOTTO}
