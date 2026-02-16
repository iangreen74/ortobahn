"""FastAPI web application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ortobahn.config import load_settings
from ortobahn.db import Database

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def get_db(request: Request) -> Database:
    """Get database from app state."""
    return request.app.state.db


def create_app() -> FastAPI:
    app = FastAPI(title="Ortobahn", description="Autonomous AI marketing engine")

    settings = load_settings()
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "https://ortobahn.com",
            "https://www.ortobahn.com",
            "https://ortobahn.vaultscaler.com",
            "https://app.ortobahn.com",
            "http://localhost:8000",
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from ortobahn.web.routes import (
        auth,
        clients,
        content,
        dashboard,
        onboard,
        payments,
        pipeline,
        sre,
        tenant_dashboard,
    )

    # Public routes (no auth required)
    app.include_router(onboard.router, prefix="/api")
    app.include_router(auth.router, prefix="/api/auth")
    app.include_router(payments.router, prefix="/api/payments")

    # Tenant self-service routes (per-client auth)
    app.include_router(tenant_dashboard.router)

    # Protected routes (admin auth dependency on each router)
    app.include_router(dashboard.router)
    app.include_router(clients.router, prefix="/clients")
    app.include_router(content.router, prefix="/content")
    app.include_router(pipeline.router, prefix="/pipeline")
    app.include_router(sre.router, prefix="/sre")

    return app
