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
        created_at = _escape(str(d.get("created_at") or ""))
        image_url = d.get("image_url") or ""
        image_html = ""
        if image_url:
            image_html = (
                f'<img src="{_escape(image_url)}" alt="Generated image" '
                f'style="max-width:200px;max-height:150px;border-radius:0.5rem;margin:0.5rem 0;">'
            )
        parts.append(
            f'<div class="draft-card">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:0.5rem;">'
            f'<span class="badge {_escape(platform)}">{_escape(platform)}</span>'
            f'<span style="display:flex;gap:0.75rem;align-items:center;">'
            f'<small style="opacity:0.5;">confidence: {confidence:.2f}</small>'
            f'<time datetime="{created_at}" style="font-size:0.75em;color:var(--text-tertiary);">{created_at[:16]}</time>'
            f"</span>"
            f"</div>"
            f"{image_html}"
            f'<p id="text-{pid}" style="margin:0.5rem 0;white-space:pre-wrap;">{text}</p>'
            f'<form id="edit-form-{pid}" method="post" action="/my/drafts/{pid}/edit" style="display:none;margin:0.5rem 0;">'
            f'<textarea name="text" rows="4" spellcheck="true" style="width:100%;margin-bottom:0.5rem;">{text}</textarea>'
            f'<div class="action-buttons">'
            f'<button type="submit">Save</button>'
            f'<button type="button" class="secondary" onclick="'
            f"document.getElementById('edit-form-{pid}').style.display='none';"
            f"document.getElementById('text-{pid}').style.display='block';"
            f"document.getElementById('edit-actions-{pid}').style.display='flex';"
            f'">Cancel</button>'
            f"</div></form>"
            f'<div id="edit-actions-{pid}" class="action-buttons" style="margin-top:0.5rem;">'
            f'<form method="post" action="/my/drafts/{pid}/approve" style="margin:0;">'
            f'<button type="submit">Publish</button>'
            f"</form>"
            f'<button type="button" class="outline" onclick="'
            f"document.getElementById('edit-form-{pid}').style.display='block';"
            f"document.getElementById('text-{pid}').style.display='none';"
            f"document.getElementById('edit-actions-{pid}').style.display='none';"
            f'">Edit</button>'
            f'<form method="post" action="/my/drafts/{pid}/reject" style="margin:0;display:flex;gap:0.25rem;align-items:center;">'
            f'<input type="text" name="reason" placeholder="Why? (optional)">'
            f'<button type="submit" class="secondary">Reject</button>'
            f"</form>"
            f"</div>"
            f"</div>"
        )

    return HTMLResponse("".join(parts))


@router.post("/drafts/{post_id}/approve")
async def tenant_approve_draft(request: Request, post_id: str, background_tasks: BackgroundTasks, client: AuthClient):
    """Approve and publish a draft post (merged approve+publish flow)."""
    db = request.app.state.db
    settings = request.app.state.settings
    # Verify the post belongs to this client
    post = db.get_post(post_id)
    if not post or post.get("client_id") != client["id"]:
        raise HTTPException(status_code=404)
    db.approve_post(post_id)

    # Record review for voice learning
    try:
        from ortobahn.memory import MemoryStore
        from ortobahn.voice_learning import VoiceLearner

        voice = VoiceLearner(db, MemoryStore(db))
        voice.record_review(
            client_id=client["id"],
            content_type="post",
            content_id=post_id,
            action="approved",
            content_snapshot={
                "text": post.get("text", ""),
                "confidence": post.get("confidence"),
                "platform": post.get("platform"),
            },
        )
    except Exception:
        logger.warning("Voice learning failed on approve (non-fatal)", exc_info=True)

    # Immediately publish the approved post
    def _publish_single():
        from ortobahn.orchestrator import Pipeline

        pipeline = Pipeline(settings)
        try:
            pipeline.publish_approved_drafts(client_id=client["id"])
        except Exception as e:
            logger.error(f"Post publish failed after approve: {e}")
        finally:
            pipeline.close()

    background_tasks.add_task(_publish_single)

    referer = request.headers.get("referer", "")
    if "/my/review" in referer:
        return RedirectResponse("/my/review", status_code=303)
    return RedirectResponse("/my/dashboard", status_code=303)


@router.post("/drafts/{post_id}/reject")
async def tenant_reject_draft(request: Request, post_id: str, client: AuthClient, reason: str = Form("")):
    db = request.app.state.db
    post = db.get_post(post_id)
    if not post or post.get("client_id") != client["id"]:
        raise HTTPException(status_code=404)
    db.reject_post(post_id)

    # Record review for voice learning
    try:
        from ortobahn.memory import MemoryStore
        from ortobahn.voice_learning import VoiceLearner

        voice = VoiceLearner(db, MemoryStore(db))
        voice.record_review(
            client_id=client["id"],
            content_type="post",
            content_id=post_id,
            action="rejected",
            rejection_reason=reason,
            content_snapshot={
                "text": post.get("text", ""),
                "confidence": post.get("confidence"),
                "platform": post.get("platform"),
            },
        )
    except Exception:
        logger.warning("Voice learning failed on reject (non-fatal)", exc_info=True)

    return RedirectResponse("/my/dashboard", status_code=303)


@router.post("/drafts/{post_id}/edit")
async def tenant_edit_draft(request: Request, post_id: str, client: AuthClient):
    """Edit a draft post's text and record the change for voice learning."""
    db = request.app.state.db
    post = db.get_post(post_id)
    if not post or post.get("client_id") != client["id"]:
        raise HTTPException(status_code=404)

    form = await request.form()
    new_text = str(form.get("text", "")).strip()
    if not new_text:
        return RedirectResponse("/my/dashboard", status_code=303)

    original_text = post.get("text", "")
    db.update_post_text(post_id, new_text)

    # Record edit for voice learning
    try:
        from ortobahn.memory import MemoryStore
        from ortobahn.voice_learning import VoiceLearner

        voice = VoiceLearner(db, MemoryStore(db))
        voice.record_review(
            client_id=client["id"],
            content_type="post",
            content_id=post_id,
            action="edited",
            content_snapshot={
                "original_text": original_text,
                "edited_text": new_text,
                "confidence": post.get("confidence"),
                "platform": post.get("platform"),
            },
        )
    except Exception:
        logger.warning("Voice learning failed on edit (non-fatal)", exc_info=True)

    return RedirectResponse("/my/dashboard", status_code=303)


# ---------------------------------------------------------------------------
# Content repurposing endpoints
# ---------------------------------------------------------------------------


@router.post("/repurpose/post-to-article/{post_id}")
async def repurpose_post_to_article(request: Request, post_id: str, client: AuthClient):
    """Create a draft article seeded from a high-performing post."""
    from ortobahn.repurposer import Repurposer

    db = request.app.state.db
    repurposer = Repurposer(db)
    article_id = repurposer.post_to_article(post_id, client["id"])

    if not article_id:
        return RedirectResponse("/my/dashboard?msg=repurpose_failed", status_code=303)

    return RedirectResponse("/my/articles?msg=article_created", status_code=303)


@router.post("/repurpose/article-to-series/{article_id}")
async def repurpose_article_to_series(
    request: Request,
    article_id: str,
    client: AuthClient,
):
    """Create a series of social posts from an article."""
    from ortobahn.repurposer import Repurposer

    db = request.app.state.db

    form = await request.form()
    platform = str(form.get("platform", "bluesky"))
    num_posts = int(str(form.get("num_posts", "3")))
    num_posts = max(2, min(5, num_posts))

    repurposer = Repurposer(db)
    post_ids = repurposer.article_to_series(article_id, client["id"], platform=platform, num_posts=num_posts)

    if not post_ids:
        return RedirectResponse("/my/dashboard?msg=repurpose_failed", status_code=303)

    return RedirectResponse("/my/dashboard?msg=series_created", status_code=303)
