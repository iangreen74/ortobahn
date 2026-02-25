"""Tenant content generation and draft management routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ortobahn.auth import AuthClient
from ortobahn.models import Platform
from ortobahn.web.routes.tenant_helpers import _run_tenant_pipeline
from ortobahn.web.utils import escape as _escape

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


@router.post("/generate")
async def tenant_generate(
    request: Request,
    background_tasks: BackgroundTasks,
    client: AuthClient,
    platforms: str = Form("bluesky"),
):
    """Trigger a pipeline run for this tenant — always auto-publishes."""
    settings = request.app.state.settings
    platform_list = [Platform(p.strip()) for p in platforms.split(",") if p.strip()]

    # Always publish — the AI is autonomous
    background_tasks.add_task(_run_tenant_pipeline, settings, client["id"], platform_list, True)

    return RedirectResponse("/my/dashboard?msg=creating", status_code=303)


def _publish_drafts(settings, client_id: str):
    """Approve all pending drafts and publish them."""
    from ortobahn.orchestrator import Pipeline

    pipeline = Pipeline(settings)
    try:
        # Approve all drafts for this client
        drafts = pipeline.db.get_drafts_for_review(client_id=client_id)
        for d in drafts:
            pipeline.db.approve_post(d["id"])
        logger.info(f"Approved {len(drafts)} drafts for {client_id}")

        # Publish approved posts
        published = pipeline.publish_approved_drafts(client_id=client_id)
        logger.info(f"Published {published} approved drafts for {client_id}")
    except Exception as e:
        logger.error(f"Bulk publish failed for {client_id}: {e}")
    finally:
        pipeline.close()


@router.post("/publish-drafts")
async def tenant_publish_drafts(
    request: Request,
    background_tasks: BackgroundTasks,
    client: AuthClient,
):
    """Approve and publish all pending drafts for this tenant."""
    settings = request.app.state.settings
    background_tasks.add_task(_publish_drafts, settings, client["id"])
    return RedirectResponse("/my/dashboard", status_code=303)


@router.get("/api/partials/drafts", response_class=HTMLResponse)
async def tenant_drafts_partial(request: Request, client: AuthClient):
    """Return pending drafts as HTML cards for review."""
    db = request.app.state.db
    drafts = db.get_drafts_for_review(client_id=client["id"])

    if not drafts:
        return HTMLResponse('<p style="opacity:0.6;text-align:center;">No pending drafts.</p>')

    parts = []
    for d in drafts:
        pid = d["id"]
        platform = d.get("platform") or "generic"
        text = _escape(d.get("text") or "")
        confidence = d.get("confidence") or 0
        parts.append(
            f'<div class="draft-card" style="border:1px solid #333;border-radius:8px;padding:1rem;margin-bottom:0.75rem;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">'
            f'<span style="background:#3b82f6;color:#fff;padding:2px 8px;border-radius:4px;font-size:0.75em;">{_escape(platform)}</span>'
            f'<span style="opacity:0.5;font-size:0.8em;">confidence: {confidence:.2f}</span>'
            f"</div>"
            f'<p style="margin:0.5rem 0;white-space:pre-wrap;">{text}</p>'
            f'<div style="display:flex;gap:0.5rem;margin-top:0.5rem;">'
            f'<form method="post" action="/my/drafts/{pid}/approve" style="margin:0;">'
            f'<button type="submit" style="padding:4px 12px;font-size:0.8em;">Approve</button>'
            f"</form>"
            f'<form method="post" action="/my/drafts/{pid}/reject" style="margin:0;">'
            f'<button type="submit" class="secondary" style="padding:4px 12px;font-size:0.8em;">Reject</button>'
            f"</form>"
            f"</div>"
            f"</div>"
        )

    return HTMLResponse("".join(parts))


@router.post("/drafts/{post_id}/approve")
async def tenant_approve_draft(request: Request, post_id: str, client: AuthClient):
    db = request.app.state.db
    # Verify the post belongs to this client
    post = db.get_post(post_id)
    if not post or post.get("client_id") != client["id"]:
        raise HTTPException(status_code=404)
    db.approve_post(post_id)
    return RedirectResponse("/my/dashboard", status_code=303)


@router.post("/drafts/{post_id}/reject")
async def tenant_reject_draft(request: Request, post_id: str, client: AuthClient):
    db = request.app.state.db
    post = db.get_post(post_id)
    if not post or post.get("client_id") != client["id"]:
        raise HTTPException(status_code=404)
    db.reject_post(post_id)
    return RedirectResponse("/my/dashboard", status_code=303)
