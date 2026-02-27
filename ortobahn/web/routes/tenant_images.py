"""Tenant images gallery — view AI-generated images for posts."""

from __future__ import annotations

import html
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ortobahn.auth import AuthClient

logger = logging.getLogger("ortobahn.web.images")

router = APIRouter(prefix="/my")


def _escape(s: str) -> str:
    return html.escape(str(s)) if s else ""


@router.get("/images")
async def tenant_images(request: Request, client: AuthClient):
    """Render the images gallery page."""
    db = request.app.state.db
    client_id = client["id"]

    # Total images generated
    total_row = db.fetchone(
        "SELECT COUNT(*) as c FROM posts WHERE client_id=? AND image_url IS NOT NULL AND image_url != ''",
        (client_id,),
    )
    total_images = total_row["c"] if total_row else 0

    # Image generation enabled?
    image_gen_enabled = bool(client.get("image_generation_enabled"))

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "tenant_images.html",
        {
            "request": request,
            "client": client,
            "total_images": total_images,
            "image_gen_enabled": image_gen_enabled,
        },
    )


@router.get("/api/partials/images-gallery", response_class=HTMLResponse)
async def images_gallery(request: Request, client: AuthClient):
    """Return images gallery as an HTML fragment."""
    db = request.app.state.db
    client_id = client["id"]

    page = 1
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except (ValueError, TypeError):
        pass

    per_page = 12
    offset = (page - 1) * per_page

    posts = db.fetchall(
        "SELECT id, text, platform, status, image_url, image_prompt, published_at, created_at"
        " FROM posts WHERE client_id=? AND image_url IS NOT NULL AND image_url != ''"
        " ORDER BY COALESCE(published_at, created_at) DESC"
        " LIMIT ? OFFSET ?",
        (client_id, per_page + 1, offset),
    )

    has_next = len(posts) > per_page
    posts = posts[:per_page]

    if not posts:
        if page == 1:
            return HTMLResponse(
                '<div class="empty-state">'
                '<div class="empty-state-icon">&#x1F5BC;</div>'
                "<p>No images generated yet.</p>"
                '<p>Enable AI image generation in <a href="/my/settings">Settings</a> '
                "and your next pipeline run will create images for posts.</p>"
                "</div>"
            )
        return HTMLResponse('<p style="text-align:center;opacity:0.5;">No more images.</p>')

    parts = ['<div class="image-gallery">']
    for p in posts:
        image_url = _escape(p.get("image_url") or "")
        platform = _escape(p.get("platform") or "generic")
        status = p.get("status") or "draft"
        status_cls = {"published": "completed", "draft": "draft", "failed": "failed"}.get(status, "draft")
        text = _escape((p.get("text") or "")[:120])
        prompt = _escape((p.get("image_prompt") or "")[:100])
        ts = p.get("published_at") or p.get("created_at") or ""

        parts.append(
            f'<div class="image-card">'
            f'<a href="{image_url}" target="_blank" rel="noopener">'
            f'<img src="{image_url}" alt="Generated image" loading="lazy">'
            f"</a>"
            f'<div class="image-card-body">'
            f'<div style="display:flex;gap:0.5rem;align-items:center;margin-bottom:0.25rem;">'
            f'<span class="badge {platform}" style="font-size:0.7rem;">{platform}</span>'
            f'<span class="badge {status_cls}" style="font-size:0.7rem;">{_escape(status)}</span>'
            f"</div>"
            f'<p class="image-card-text">{text}{"..." if len(p.get("text") or "") > 120 else ""}</p>'
        )
        if prompt:
            parts.append(
                f'<p class="image-card-prompt">Prompt: {prompt}{"..." if len(p.get("image_prompt") or "") > 100 else ""}</p>'
            )
        if ts:
            parts.append(
                f'<time datetime="{_escape(str(ts))}" style="font-size:0.7rem;color:var(--text-tertiary);">{_escape(str(ts)[:16])}</time>'
            )
        parts.append("</div></div>")

    parts.append("</div>")

    # Pagination
    if page > 1 or has_next:
        parts.append('<div style="display:flex;justify-content:center;gap:1rem;margin-top:1.5rem;">')
        if page > 1:
            parts.append(
                f'<button class="outline" hx-get="/my/api/partials/images-gallery?page={page - 1}"'
                ' hx-target="#images-grid" hx-swap="innerHTML">&larr; Newer</button>'
            )
        if has_next:
            parts.append(
                f'<button class="outline" hx-get="/my/api/partials/images-gallery?page={page + 1}"'
                ' hx-target="#images-grid" hx-swap="innerHTML">Older &rarr;</button>'
            )
        parts.append("</div>")

    return HTMLResponse("".join(parts))
