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
    # `demo_credentials` tells the login page whether it may show the demo
    # account hint. It is true ONLY while the seeded passwords are still the
    # published defaults — configure SMS2_ADMIN_PASSWORD / SMS2_SEED_PASSWORD
    # and the hint disappears on its own. No secret is exposed here: the flag
    # says whether the PUBLIC defaults are in use, never what the password is.
    from .seed import SEED_PASSWORDS_ARE_DEFAULT
    return {"app": APP_NAME, "version": APP_VERSION, "status": "ok",
            "motto": APP_MOTTO, "demo_credentials": SEED_PASSWORDS_ARE_DEFAULT}
