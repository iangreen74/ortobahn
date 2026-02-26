"""Tenant Review Queue — unified draft review page."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import RedirectResponse

from ortobahn.auth import AuthClient

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


@router.get("/review")
async def tenant_review(request: Request, client: AuthClient):
    """Full Review Queue page — drafts + engagement replies."""
    db = request.app.state.db
    templates = request.app.state.templates
    client_id = client["id"]

    drafts = db.get_drafts_for_review(client_id=client_id)

    engagement_drafts = db.fetchall(
        "SELECT id, notification_text, reply_text, confidence, platform, created_at"
        " FROM engagement_replies"
        " WHERE client_id=? AND status='draft'"
        " ORDER BY created_at DESC LIMIT 20",
        (client_id,),
    )

    return templates.TemplateResponse(
        "tenant_review.html",
        {
            "request": request,
            "client": client,
            "drafts": drafts,
            "engagement_drafts": engagement_drafts,
        },
    )


@router.post("/review/publish-all")
async def tenant_publish_all(
    request: Request,
    background_tasks: BackgroundTasks,
    client: AuthClient,
):
    """Approve and publish all pending drafts."""
    from ortobahn.web.routes.tenant_content import _publish_drafts

    settings = request.app.state.settings
    background_tasks.add_task(_publish_drafts, settings, client["id"])
    return RedirectResponse("/my/review", status_code=303)
