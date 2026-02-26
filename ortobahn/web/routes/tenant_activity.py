"""Tenant Activity page — unified timeline of pipeline runs and events."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ortobahn.auth import AuthClient

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


@router.get("/activity")
async def tenant_activity(request: Request, client: AuthClient):
    """Activity page — pipeline runs, publish events, review actions."""
    db = request.app.state.db
    templates = request.app.state.templates
    client_id = client["id"]

    # Recent pipeline runs
    pipeline_runs = db.fetchall(
        "SELECT id, status, started_at, completed_at, posts_published,"
        " total_input_tokens, total_output_tokens, errors"
        " FROM pipeline_runs WHERE client_id=?"
        " ORDER BY started_at DESC LIMIT 20",
        (client_id,),
    )

    # Recent post events (published, approved, rejected)
    recent_events = db.fetchall(
        "SELECT id, text, platform, status, published_at, created_at"
        " FROM posts WHERE client_id=? AND status IN ('published', 'approved', 'rejected')"
        " ORDER BY COALESCE(published_at, created_at) DESC LIMIT 30",
        (client_id,),
    )

    return templates.TemplateResponse(
        "tenant_activity.html",
        {
            "request": request,
            "client": client,
            "pipeline_runs": pipeline_runs,
            "recent_events": recent_events,
        },
    )
