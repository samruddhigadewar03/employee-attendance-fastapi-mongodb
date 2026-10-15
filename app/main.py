"""
HROne Employee Attendance & Analytics API
Entry point — FastAPI application with lifespan management.

Run with:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.database import connect_db, disconnect_db
from app.routers import analytics, attendance, employees

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan: database connect on startup, disconnect on shutdown.
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up HROne API …")
    connect_db()
    yield
    logger.info("Shutting down HROne API …")
    disconnect_db()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="HROne Employee Attendance & Analytics API",
    description=(
        "REST API for employee attendance management: punch-in/out, "
        "manual corrections with full audit trail, and MongoDB-powered analytics."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Allow the local frontend to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(analytics.router)


# ---------------------------------------------------------------------------
# Global error handler — prevents stack traces leaking to clients.
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception for %s %s", request.method, request.url)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again later."},
    )


# ---------------------------------------------------------------------------
# Health-check endpoint
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Health"], summary="Health check")
def health():
    """Returns 200 when the API is running and the database is reachable."""
    from app.database import get_db
    try:
        get_db().command("ping")
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": str(exc)},
        )


# ---------------------------------------------------------------------------
# Serve the frontend (static files) at /ui
# ---------------------------------------------------------------------------

import os, pathlib

_frontend_dir = pathlib.Path(__file__).parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
