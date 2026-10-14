"""FastAPI application entry point.

Run with:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import ensure_data_dir_permissions
from .config import settings
from .db import SessionLocal, init_db
from .api import auth_api, chat_api, design, exports_api, images_api, projects, settings_api, ws_api
from .services import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("jarvis")

app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="AI-powered home design platform: deterministic layout engine, interactive 2D "
                "and 3D editors, budget estimation and honest AI assistance. Preliminary design "
                "aid only -- not a construction document.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()] or ["*"],
    allow_credentials=settings.cors_origins != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    session = SessionLocal()
    try:
        storage.ensure_local_user(session)
        session.commit()
    finally:
        session.close()
    ensure_data_dir_permissions()
    log.info("storage ready at %s (auth_enabled=%s)", settings.data_dir, settings.auth_enabled)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "app": settings.app_name, "version": settings.version,
            "auth_enabled": settings.auth_enabled,
            "database": "sqlite" if "sqlite" in settings.database_url else "postgresql",
            "notice": "Preliminary design aid. Outputs require review by licensed professionals."}


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


app.include_router(auth_api.router)
app.include_router(projects.router)
app.include_router(design.router)
app.include_router(images_api.router)
app.include_router(chat_api.router)
app.include_router(exports_api.router)
app.include_router(settings_api.router)
app.include_router(ws_api.router)

# Serve the built frontend when present (single-container deployments). In dev the
# Vite server on :5173 proxies /api and /ws here instead.
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
