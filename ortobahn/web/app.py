"""FastAPI web application factory."""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ortobahn.auth import _LoginRedirect
from ortobahn.cognito import CognitoClient
from ortobahn.config import load_settings
from ortobahn.db import Database, create_database
from ortobahn.web.rate_limit import RateLimitMiddleware

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def get_db(request: Request) -> Database:
    """Get database from app state."""
    return request.app.state.db


def create_app() -> FastAPI:
    app = FastAPI(title="Ortobahn", description="Autonomous AI marketing engine")

    settings = load_settings()
    app.state.settings = settings
    app.state.db = create_database(settings)
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    if settings.cognito_user_pool_id and settings.cognito_client_id:
        app.state.cognito = CognitoClient(
            settings.cognito_user_pool_id,
            settings.cognito_client_id,
            settings.cognito_region,
        )
    else:
        app.state.cognito = None

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

    app.add_middleware(
        RateLimitMiddleware,
        enabled=settings.rate_limit_enabled,
        default_rpm=settings.rate_limit_default,
    )

    @app.get("/health")
    async def health():
        """ALB health check — verifies DB connectivity and reports deploy info."""
        try:
            db = app.state.db
            db.fetchone("SELECT 1 AS ok")
            result: dict = {"status": "healthy", "db": db.backend}
            deploy_sha = os.environ.get("DEPLOY_SHA", "")
            if deploy_sha:
                result["sha"] = deploy_sha
            environment = os.environ.get("ENVIRONMENT", "production")
            result["environment"] = environment
            return result
        except Exception as e:
            return JSONResponse({"status": "unhealthy", "error": str(e)}, status_code=503)

    @app.post("/api/deploy/register")
    async def register_deploy(request: Request):
        """Record a deployment for tracking. Called by CI/CD pipeline."""
        deploy_key = os.environ.get("ORTOBAHN_SECRET_KEY", "")
        auth_header = request.headers.get("authorization", "")
        if not deploy_key or auth_header != f"Bearer {deploy_key}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        body = await request.json()
        sha = body.get("sha", "")
        environment = body.get("environment", "production")
        if not sha:
            return JSONResponse({"error": "sha required"}, status_code=400)

        db = app.state.db
        current = db.get_current_deploy(environment)
        previous_sha = current["sha"] if current else None
        deploy_id = db.record_deploy(sha=sha, environment=environment, previous_sha=previous_sha)
        return {"deploy_id": deploy_id, "sha": sha, "previous_sha": previous_sha}

    @app.exception_handler(_LoginRedirect)
    async def _redirect_to_login(request: Request, exc: _LoginRedirect):
        return RedirectResponse(f"/api/auth/login?next={exc.next_url}")

    @app.middleware("http")
    async def access_log_middleware(request: Request, call_next):
        """Log non-trivial HTTP requests for security monitoring."""
        path = request.url.path
        # Skip static, health, and glass polling to keep volume low
        if path.startswith("/static") or path == "/health" or path.startswith("/glass/api/"):
            return await call_next(request)

        start = time.time()
        response = await call_next(request)
        elapsed_ms = (time.time() - start) * 1000

        # Only log suspicious or non-200 requests to keep DB lean
        is_suspicious = any(probe in path.lower() for probe in (".env", "/admin", "/wp-", "/phpmyadmin", "/.git"))
        if is_suspicious or response.status_code >= 400:
            try:
                db = request.app.state.db
                db.execute(
                    "INSERT INTO access_logs (id, method, path, status_code, source_ip, user_agent, response_time_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        request.method,
                        path[:500],
                        response.status_code,
                        request.client.host if request.client else "",
                        (request.headers.get("user-agent") or "")[:500],
                        elapsed_ms,
                    ),
                    commit=True,
                )
            except Exception:
                pass  # Never let logging break a request

        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from ortobahn.web.routes import (
        auth,
        chat,
        clients,
        content,
        dashboard,
        glass,
        legal,
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
    app.include_router(glass.router)
    app.include_router(legal.router)

    # Tenant self-service routes (per-client auth)
    app.include_router(tenant_dashboard.router)
    app.include_router(chat.router)

    # Protected routes (admin auth dependency on each router)
    app.include_router(dashboard.router)
    app.include_router(clients.router, prefix="/clients")
    app.include_router(content.router, prefix="/content")
    app.include_router(pipeline.router, prefix="/pipeline")
    app.include_router(sre.router, prefix="/sre")

    return app
