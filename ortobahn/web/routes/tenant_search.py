"""Tenant AI search — sidebar search box endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ortobahn.auth import AuthClient
from ortobahn.web.utils import escape as _escape

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")

# Page index for fuzzy matching
_PAGES = [
    {"title": "Home", "url": "/my/dashboard", "keywords": "home dashboard overview kpi"},
    {"title": "Review Queue", "url": "/my/review", "keywords": "review queue drafts approve reject"},
    {"title": "Posts", "url": "/my/posts", "keywords": "posts social content published"},
    {"title": "Articles", "url": "/my/articles", "keywords": "articles long-form blog"},
    {"title": "Calendar", "url": "/my/calendar", "keywords": "calendar schedule timeline"},
    {"title": "Performance", "url": "/my/performance", "keywords": "performance analytics metrics engagement"},
    {"title": "Activity", "url": "/my/activity", "keywords": "activity log pipeline runs events"},
    {"title": "Settings", "url": "/my/settings", "keywords": "settings brand voice platforms credentials"},
    {
        "title": "Settings > Platforms",
        "url": "/my/settings/platforms",
        "keywords": "platforms bluesky twitter linkedin credentials connect",
    },
    {
        "title": "Settings > Automation",
        "url": "/my/settings/automation",
        "keywords": "automation auto-publish frequency schedule",
    },
    {"title": "Settings > Billing", "url": "/my/settings/billing", "keywords": "billing subscription plan payment"},
]


@router.get("/search", response_class=HTMLResponse)
async def tenant_search(request: Request, client: AuthClient, q: str = ""):
    """Search pages, content, and settings."""
    query = q.strip().lower()
    if not query:
        return HTMLResponse("")

    parts: list[str] = []

    # 1. Page matches
    page_matches = [
        p for p in _PAGES if query in p["title"].lower() or any(query in kw for kw in p["keywords"].split())
    ]
    if page_matches:
        parts.append('<div class="search-section"><small style="color:var(--text-tertiary);">PAGES</small></div>')
        for p in page_matches[:5]:
            parts.append(
                f'<a href="{p["url"]}" class="search-result-item">'
                f'<div class="result-title">{_escape(p["title"])}</div>'
                f"</a>"
            )

    # 2. Content search (posts + articles)
    db = request.app.state.db
    posts = db.fetchall(
        "SELECT id, text, platform, status FROM posts WHERE client_id=? AND text LIKE ? ORDER BY created_at DESC LIMIT 5",
        (client["id"], f"%{query}%"),
    )
    if posts:
        parts.append(
            '<div class="search-section" style="margin-top:0.75rem;"><small style="color:var(--text-tertiary);">POSTS</small></div>'
        )
        for p in posts:
            text = _escape((p.get("text") or "")[:80])
            status = p.get("status") or "draft"
            parts.append(
                f'<a href="/my/posts?highlight={_escape(p["id"])}" class="search-result-item">'
                f'<div class="result-title">{text}</div>'
                f'<div class="result-desc">{_escape(p.get("platform") or "generic")} &middot; {_escape(status)}</div>'
                f"</a>"
            )

    articles = db.fetchall(
        "SELECT id, title, status FROM articles WHERE client_id=? AND (title LIKE ? OR body_markdown LIKE ?) ORDER BY created_at DESC LIMIT 5",
        (client["id"], f"%{query}%", f"%{query}%"),
    )
    if articles:
        parts.append(
            '<div class="search-section" style="margin-top:0.75rem;"><small style="color:var(--text-tertiary);">ARTICLES</small></div>'
        )
        for a in articles:
            title = _escape(a.get("title") or "Untitled")
            status = a.get("status") or "draft"
            parts.append(
                f'<a href="/my/articles/{_escape(a["id"])}" class="search-result-item">'
                f'<div class="result-title">{title}</div>'
                f'<div class="result-desc">{_escape(status)}</div>'
                f"</a>"
            )

    if not parts:
        parts.append(
            '<div style="text-align:center;padding:2rem;color:var(--text-tertiary);">'
            f"No results for &ldquo;{_escape(query)}&rdquo;"
            "</div>"
        )

    return HTMLResponse("".join(parts))


@router.get("/api/pipeline-pulse", response_class=HTMLResponse)
async def pipeline_pulse(request: Request, client: AuthClient):
    """Return a pulse dot if pipeline is running, empty otherwise."""
    db = request.app.state.db
    running = db.fetchone(
        "SELECT id FROM pipeline_runs WHERE status='running' AND client_id=? LIMIT 1",
        (client["id"],),
    )
    if running:
        return HTMLResponse('<a href="/my/activity" title="Pipeline running"><span class="pipeline-dot"></span></a>')
    return HTMLResponse("")


@router.get("/api/review-count", response_class=HTMLResponse)
async def review_count(request: Request, client: AuthClient):
    """Return review queue badge count."""
    db = request.app.state.db
    row = db.fetchone(
        "SELECT COUNT(*) as c FROM posts WHERE status='draft' AND client_id=?",
        (client["id"],),
    )
    count = row["c"] if row else 0
    if count > 0:
        return HTMLResponse(str(count))
    return HTMLResponse("")
