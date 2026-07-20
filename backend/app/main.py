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
    return {"app": APP_NAME, "version": APP_VERSION, "status": "ok",
            "motto": APP_MOTTO}
