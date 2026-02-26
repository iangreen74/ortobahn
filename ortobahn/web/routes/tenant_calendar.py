"""Tenant content calendar — visual timeline of scheduled and published content."""

from __future__ import annotations

import calendar
import html
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ortobahn.auth import AuthClient

logger = logging.getLogger("ortobahn.web.calendar")

router = APIRouter(prefix="/my")


def _escape(s: str) -> str:
    return html.escape(str(s)) if s else ""


# ---------------------------------------------------------------------------
# Badge colours by status
# ---------------------------------------------------------------------------

_STATUS_CLASS = {
    "published": "completed",
    "draft": "draft",
    "approved": "running",
    "scheduled": "running",
    "failed": "failed",
    "rejected": "failed",
}

_PLATFORM_EMOJI = {
    "bluesky": "&#x1F30A;",
    "twitter": "&#x1F426;",
    "linkedin": "&#x1F4BC;",
    "reddit": "&#x1F4AC;",
    "generic": "&#x1F4DD;",
}


# ---------------------------------------------------------------------------
# Full calendar page
# ---------------------------------------------------------------------------


@router.get("/calendar")
async def tenant_calendar(request: Request, client: AuthClient):
    """Render the full content calendar page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "tenant_calendar.html",
        {"request": request, "client": client},
    )


# ---------------------------------------------------------------------------
# HTMX partials
# ---------------------------------------------------------------------------


@router.get("/api/partials/calendar", response_class=HTMLResponse)
async def calendar_grid(request: Request, client: AuthClient):
    """Return the calendar month grid as an HTML fragment."""
    db = request.app.state.db
    client_id = client["id"]

    # Parse month/year from query params (default: current month)
    now = datetime.now(timezone.utc)
    try:
        year = int(request.query_params.get("year", now.year))
        month = int(request.query_params.get("month", now.month))
    except (ValueError, TypeError):
        year, month = now.year, now.month

    # Clamp
    if month < 1:
        month, year = 12, year - 1
    elif month > 12:
        month, year = 1, year + 1

    # Get first/last day of month
    _, days_in_month = calendar.monthrange(year, month)
    month_start = f"{year}-{month:02d}-01"
    month_end = f"{year}-{month:02d}-{days_in_month:02d}T23:59:59"

    # Fetch posts in this month (published or scheduled)
    posts = db.fetchall(
        "SELECT id, text, status, platform, confidence, published_at, scheduled_at, created_at"
        " FROM posts WHERE client_id=?"
        " AND (published_at BETWEEN ? AND ? OR scheduled_at BETWEEN ? AND ?"
        " OR (status='draft' AND created_at BETWEEN ? AND ?))"
        " ORDER BY COALESCE(scheduled_at, published_at, created_at)",
        (client_id, month_start, month_end, month_start, month_end, month_start, month_end),
    )

    # Group posts by day
    day_posts: dict[int, list[dict]] = {}
    for p in posts:
        # Determine which day this post belongs to
        ts = p.get("scheduled_at") or p.get("published_at") or p.get("created_at") or ""
        if not ts:
            continue
        try:
            day = int(ts[8:10])
        except (ValueError, IndexError):
            continue
        day_posts.setdefault(day, []).append(dict(p))

    # Build calendar grid
    month_name = calendar.month_name[month]
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    # Header with navigation
    parts = [
        '<div class="calendar-container">',
        '<div class="calendar-header">',
        f'<button class="outline" hx-get="/my/api/partials/calendar?year={prev_year}&month={prev_month}"'
        ' hx-target="#calendar-grid" hx-swap="innerHTML">&larr;</button>',
        f"<h3>{month_name} {year}</h3>",
        f'<button class="outline" hx-get="/my/api/partials/calendar?year={next_year}&month={next_month}"'
        ' hx-target="#calendar-grid" hx-swap="innerHTML">&rarr;</button>',
        "</div>",
    ]

    # Day-of-week headers
    parts.append('<div class="calendar-grid">')
    for dow in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
        parts.append(f'<div class="calendar-dow">{dow}</div>')

    # Leading empty cells
    first_weekday = calendar.monthrange(year, month)[0]  # 0=Monday
    for _ in range(first_weekday):
        parts.append('<div class="calendar-cell empty"></div>')

    # Day cells
    today = now.day if now.year == year and now.month == month else -1
    for day in range(1, days_in_month + 1):
        is_today = "today" if day == today else ""
        cell_posts = day_posts.get(day, [])
        parts.append(f'<div class="calendar-cell {is_today}">')
        parts.append(f'<span class="calendar-day-num">{day}</span>')

        if cell_posts:
            parts.append('<div class="calendar-dots">')
            for cp in cell_posts[:4]:  # max 4 dots per day
                status_cls = _STATUS_CLASS.get(cp.get("status", ""), "draft")
                platform = cp.get("platform") or "generic"
                emoji = _PLATFORM_EMOJI.get(platform, "&#x1F4DD;")
                text_preview = _escape((cp.get("text") or "")[:40])
                parts.append(f'<span class="calendar-dot badge {status_cls}" title="{text_preview}">{emoji}</span>')
            if len(cell_posts) > 4:
                parts.append(f'<span class="calendar-more">+{len(cell_posts) - 4}</span>')
            parts.append("</div>")

        parts.append("</div>")

    # Trailing empty cells
    total_cells = first_weekday + days_in_month
    trailing = (7 - total_cells % 7) % 7
    for _ in range(trailing):
        parts.append('<div class="calendar-cell empty"></div>')

    parts.append("</div>")  # close grid
    parts.append("</div>")  # close container

    return HTMLResponse("".join(parts))


@router.get("/api/calendar/events", response_class=JSONResponse)
async def calendar_events(request: Request, client: AuthClient):
    """Return posts as JSON events for the current month."""
    db = request.app.state.db
    client_id = client["id"]

    now = datetime.now(timezone.utc)
    try:
        year = int(request.query_params.get("year", now.year))
        month = int(request.query_params.get("month", now.month))
    except (ValueError, TypeError):
        year, month = now.year, now.month

    _, days_in_month = calendar.monthrange(year, month)
    month_start = f"{year}-{month:02d}-01"
    month_end = f"{year}-{month:02d}-{days_in_month:02d}T23:59:59"

    posts = db.fetchall(
        "SELECT id, text, status, platform, confidence, published_at, scheduled_at, created_at"
        " FROM posts WHERE client_id=?"
        " AND (published_at BETWEEN ? AND ? OR scheduled_at BETWEEN ? AND ?)"
        " ORDER BY COALESCE(scheduled_at, published_at, created_at)",
        (client_id, month_start, month_end, month_start, month_end),
    )

    events = []
    for p in posts:
        events.append(
            {
                "id": p["id"],
                "text": (p.get("text") or "")[:100],
                "status": p.get("status", ""),
                "platform": p.get("platform", ""),
                "confidence": p.get("confidence", 0),
                "date": p.get("scheduled_at") or p.get("published_at") or p.get("created_at"),
            }
        )

    return JSONResponse(events)


@router.post("/api/calendar/reschedule/{post_id}")
async def reschedule_post(request: Request, post_id: str, client: AuthClient):
    """Reschedule a draft/scheduled post to a new time."""
    db = request.app.state.db
    client_id = client["id"]

    form = await request.form()
    new_time = form.get("scheduled_at", "")

    if not new_time:
        return JSONResponse({"error": "scheduled_at is required"}, status_code=400)

    # Validate post exists and belongs to client
    post = db.fetchone(
        "SELECT id, status FROM posts WHERE id=? AND client_id=?",
        (post_id, client_id),
    )
    if not post:
        return JSONResponse({"error": "Post not found"}, status_code=404)

    if post["status"] not in ("draft", "approved", "scheduled"):
        return JSONResponse(
            {"error": f"Cannot reschedule a {post['status']} post"},
            status_code=400,
        )

    db.execute(
        "UPDATE posts SET scheduled_at=? WHERE id=? AND client_id=?",
        (new_time, post_id, client_id),
        commit=True,
    )

    logger.info("Post %s rescheduled to %s by client %s", post_id, new_time, client_id)
    return JSONResponse({"ok": True, "post_id": post_id, "scheduled_at": new_time})
