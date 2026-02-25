"""Tenant HTMX polling partial routes."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ortobahn.auth import AuthClient
from ortobahn.web.utils import PIPELINE_STEPS, STEP_LABELS
from ortobahn.web.utils import badge as _badge
from ortobahn.web.utils import escape as _escape
from ortobahn.web.utils import step_index as _step_index

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


@router.get("/api/pipeline-status", response_class=HTMLResponse)
async def tenant_pipeline_status(request: Request, client: AuthClient):
    """Live pipeline status — polled every 5s by the dashboard."""
    db = request.app.state.db

    running = db.fetchone(
        "SELECT id, started_at FROM pipeline_runs"
        " WHERE status='running' AND client_id=?"
        " ORDER BY started_at DESC LIMIT 1",
        (client["id"],),
    )

    if running:
        latest_agent = db.fetchone(
            "SELECT agent_name FROM agent_logs WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
            (running["id"],),
        )
        step_name = latest_agent["agent_name"] if latest_agent else "initializing"
        step_num = _step_index(step_name) if latest_agent else 0
        total_steps = len(PIPELINE_STEPS)
        step_label = STEP_LABELS.get(step_name.lower().strip(), _escape(step_name))
        html = (
            '<div class="glass-status-card">'
            '<span class="glass-pulse running"></span>'
            f" <strong>Your AI is working</strong> &mdash; {_escape(step_label)}"
            f" ({step_num}/{total_steps})"
            "</div>"
        )
    else:
        last = db.fetchone(
            "SELECT status, completed_at, posts_published FROM pipeline_runs"
            " WHERE status IN ('completed','failed') AND client_id=?"
            " ORDER BY completed_at DESC LIMIT 1",
            (client["id"],),
        )
        drafts_row = db.fetchone(
            "SELECT COUNT(*) as c FROM posts WHERE status='draft' AND client_id=?",
            (client["id"],),
        )
        draft_count = drafts_row["c"] if drafts_row else 0
        draft_note = f" &middot; {draft_count} draft(s) pending review" if draft_count else ""

        if last and last["status"] == "failed":
            fail_ts = str(last.get("completed_at") or "unknown")
            html = (
                '<div class="glass-status-card">'
                '<span class="glass-pulse failed"></span>'
                f" <strong>Last run had an issue</strong> &mdash;"
                f' <time datetime="{_escape(fail_ts)}">{_escape(fail_ts[:16])}</time>.'
                f" This can happen during setup. Try creating content again."
                f"{draft_note}"
                "</div>"
            )
        elif last:
            published = last.get("posts_published") or 0
            html = (
                '<div class="glass-status-card">'
                '<span class="glass-pulse idle"></span>'
                f" <strong>Content engine idle</strong> &mdash; last run published {published} post(s)"
                f"{draft_note}"
                "</div>"
            )
        else:
            html = (
                '<div class="glass-status-card">'
                '<span class="glass-pulse idle"></span>'
                " <strong>Content engine idle</strong> &mdash; awaiting first run"
                "</div>"
            )

    return HTMLResponse(html)


@router.get("/api/health", response_class=HTMLResponse)
async def tenant_health(request: Request, client: AuthClient):
    """System health stats — polled every 30s by the dashboard."""
    db = request.app.state.db
    cid = client["id"]

    # Posts published in last 24h — the metric that matters
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    published_24h = db.fetchone(
        "SELECT COUNT(*) as c FROM posts WHERE status='published' AND client_id=? AND created_at >= ?",
        (cid, cutoff_24h),
    )
    pub_count = published_24h["c"] if published_24h else 0

    # Drafts waiting — shows content IS being generated even if not published
    drafts_row = db.fetchone(
        "SELECT COUNT(*) as c FROM posts WHERE status='draft' AND client_id=?",
        (cid,),
    )
    draft_count = drafts_row["c"] if drafts_row else 0

    # Last successful publish timestamp
    last_pub = db.fetchone(
        "SELECT published_at FROM posts WHERE status='published' AND client_id=? ORDER BY published_at DESC LIMIT 1",
        (cid,),
    )
    if last_pub and last_pub.get("published_at"):
        last_pub_display = _escape(str(last_pub["published_at"])[:16])
    else:
        last_pub_display = "never"

    # Pipeline runs with actual output vs empty runs (last 7 days only)
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    runs_row = db.fetchone(
        "SELECT COUNT(*) as total,"
        " SUM(CASE WHEN posts_published > 0 THEN 1 ELSE 0 END) as productive"
        " FROM pipeline_runs WHERE client_id=? AND started_at >= ?",
        (cid, cutoff_7d),
    )
    total_runs = runs_row["total"] if runs_row else 0
    productive = runs_row["productive"] if runs_row and runs_row["productive"] else 0

    if pub_count > 0:
        pub_badge = "completed"
    elif draft_count > 0:
        pub_badge = "running"
    else:
        pub_badge = "failed"

    html = (
        '<div class="grid">'
        f'<div class="glass-stat"><div class="value">'
        f'<span class="badge {pub_badge}">{pub_count}</span></div>'
        f'<div class="label">Published (24h)</div></div>'
        f'<div class="glass-stat"><div class="value">{draft_count}</div>'
        '<div class="label">Drafts pending</div></div>'
        f'<div class="glass-stat"><div class="value">{productive}/{total_runs}</div>'
        '<div class="label">Productive runs (7d)</div></div>'
        "</div>"
        f'<small style="opacity:0.6">Last published: {last_pub_display}</small>'
    )
    return HTMLResponse(html)


@router.get("/api/watchdog", response_class=HTMLResponse)
async def tenant_watchdog(request: Request, client: AuthClient):
    """Watchdog activity — polled every 30s by the dashboard."""
    db = request.app.state.db

    checks = db.fetchall(
        "SELECT probe, status, detail, created_at FROM health_checks"
        " WHERE client_id=? ORDER BY created_at DESC LIMIT 10",
        (client["id"],),
    )

    rems = db.fetchall(
        "SELECT finding_type, action, success, verified, created_at FROM watchdog_remediations"
        " WHERE client_id=? ORDER BY created_at DESC LIMIT 5",
        (client["id"],),
    )

    if not checks and not rems:
        return HTMLResponse('<p style="opacity:0.6">Watchdog: all systems normal. No issues detected.</p>')

    parts = []

    if rems:
        parts.append("<h4>Recent Remediations</h4>")
        for r in rems:
            icon = "completed" if r.get("success") else "failed"
            verified = ""
            if r.get("verified") is not None:
                verified = " (verified)" if r["verified"] else " (unverified)"
            parts.append(
                f'<div style="margin-bottom:0.5rem">'
                f"{_badge(icon)} {_escape(r.get('action') or r.get('finding_type', ''))}"
                f"<small>{verified} &mdash; {_escape(str(r.get('created_at', '')))}</small>"
                f"</div>"
            )

    if checks:
        parts.append("<h4>Recent Health Checks</h4>")
        for c in checks:
            parts.append(
                f'<div style="margin-bottom:0.25rem">'
                f"{_badge(c['status'])} <strong>{_escape(c['probe'])}</strong>"
                f" &mdash; {_escape(c.get('detail') or '')}"
                f' <small style="opacity:0.6">{_escape(str(c.get("created_at", "")))}</small>'
                f"</div>"
            )

    return HTMLResponse("".join(parts))


# ---------------------------------------------------------------------------
# HTMX partial endpoints for auto-refresh (dashboard panels)
# ---------------------------------------------------------------------------


@router.get("/api/partials/kpi", response_class=HTMLResponse)
async def tenant_kpi_partial(request: Request, client: AuthClient):
    """Return KPI cards HTML fragment for the tenant dashboard."""
    db = request.app.state.db
    posts = db.get_recent_posts_with_metrics(limit=100, client_id=client["id"])
    total_published = len([p for p in posts if p.get("status") == "published"])

    # Total engagement
    total_engagement = sum(
        (p.get("like_count") or 0) + (p.get("repost_count") or 0) for p in posts if p.get("status") == "published"
    )

    # Connected platforms
    connected = []
    for plat in ("bluesky", "twitter", "linkedin", "medium", "substack", "reddit"):
        row = db.fetchone(
            "SELECT id FROM platform_credentials WHERE client_id=? AND platform=?",
            (client["id"], plat),
        )
        if row:
            connected.append(plat)

    # Engagement trend
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    cutoff_14d = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    _MJ2 = (
        " LEFT JOIN metrics m ON p.id = m.post_id"
        " AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
    )
    this_week = db.fetchone(
        "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ2 + " WHERE p.status='published' AND p.client_id=? AND p.published_at >= ?",
        (client["id"], cutoff_7d),
    )
    last_week = db.fetchone(
        "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p"
        + _MJ2
        + " WHERE p.status='published' AND p.client_id=? AND p.published_at >= ? AND p.published_at < ?",
        (client["id"], cutoff_14d, cutoff_7d),
    )
    this_avg = (this_week["avg_eng"] or 0) if this_week else 0
    last_avg = (last_week["avg_eng"] or 0) if last_week else 0
    trend_pct = round(((this_avg - last_avg) / last_avg) * 100) if last_avg > 0 else 0

    if trend_pct > 0:
        trend_html = f'<span style="color:#10b981;">&#9650; {trend_pct}%</span>'
    elif trend_pct < 0:
        trend_html = f'<span style="color:#ef4444;">&#9660; {abs(trend_pct)}%</span>'
    else:
        trend_html = '<span style="opacity:0.6;">&mdash;</span>'

    html = (
        '<article class="kpi-card">'
        f'<div class="kpi-value">{total_published}</div>'
        '<div class="kpi-label">Published</div></article>'
        '<article class="kpi-card">'
        f'<div class="kpi-value">{total_engagement}</div>'
        '<div class="kpi-label">Engagement</div></article>'
        '<article class="kpi-card">'
        f'<div class="kpi-value">{len(connected)}</div>'
        '<div class="kpi-label">Platforms</div></article>'
        '<article class="kpi-card">'
        f'<div class="kpi-value">{trend_html}</div>'
        '<div class="kpi-label">Weekly Trend</div></article>'
    )
    return HTMLResponse(html)


@router.get("/api/partials/activity", response_class=HTMLResponse)
async def tenant_activity_feed(request: Request, client: AuthClient):
    """Return recent activity as a beautiful feed."""
    db = request.app.state.db
    cid = client["id"]

    # Recent published posts (recent by published_at or created_at)
    published = db.fetchall(
        "SELECT p.text, p.platform, p.published_at, p.status, p.created_at,"
        " COALESCE(m.like_count,0) as likes, COALESCE(m.repost_count,0) as reposts"
        " FROM posts p LEFT JOIN metrics m ON p.id = m.post_id"
        " AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
        " WHERE p.client_id=? AND p.status='published'"
        " ORDER BY COALESCE(p.published_at, p.created_at) DESC LIMIT 10",
        (cid,),
    )

    # Recent articles
    articles = db.fetchall(
        "SELECT title, status, word_count, created_at FROM articles WHERE client_id=? ORDER BY created_at DESC LIMIT 3",
        (cid,),
    )

    # Recent pipeline runs
    runs = db.fetchall(
        "SELECT status, posts_published, started_at, completed_at FROM pipeline_runs"
        " WHERE client_id=? ORDER BY started_at DESC LIMIT 3",
        (cid,),
    )

    if not published and not articles and not runs:
        return HTMLResponse(
            '<div class="activity-empty">'
            "<p>Your AI is ready to create. Click <strong>Create Content</strong> to get started.</p>"
            "</div>"
        )

    parts = []

    for p in published:
        text = _escape((p.get("text") or "")[:120])
        if len(p.get("text") or "") > 120:
            text += "..."
        platform = _escape(p.get("platform") or "bluesky")
        likes = p.get("likes") or 0
        reposts = p.get("reposts") or 0
        raw_ts = str(p.get("published_at") or p.get("created_at") or "")
        ts = _escape(raw_ts[:16])
        engagement = ""
        if likes or reposts:
            engagement = (
                f'<span class="activity-stats">'
                f"{likes} like{'s' if likes != 1 else ''}"
                f" &middot; {reposts} repost{'s' if reposts != 1 else ''}"
                f"</span>"
            )
        parts.append(
            f'<div class="activity-item">'
            f'<div class="activity-dot published"></div>'
            f'<div class="activity-body">'
            f'<div class="activity-header">'
            f'<span class="badge {_escape(platform)}">{platform}</span>'
            f'<time datetime="{_escape(raw_ts)}" class="activity-time">{ts}</time>'
            f"</div>"
            f'<p class="activity-text">{text}</p>'
            f"{engagement}"
            f"</div></div>"
        )

    for a in articles:
        title = _escape(a.get("title") or "Untitled")
        status = a.get("status") or "draft"
        words = a.get("word_count") or 0
        raw_ts = str(a.get("created_at") or "")
        ts = _escape(raw_ts[:16])
        icon_class = "published" if status == "published" else "draft" if status == "draft" else "running"
        parts.append(
            f'<div class="activity-item">'
            f'<div class="activity-dot {icon_class}"></div>'
            f'<div class="activity-body">'
            f'<div class="activity-header">'
            f'<span class="badge draft">article</span>'
            f'<time datetime="{_escape(raw_ts)}" class="activity-time">{ts}</time>'
            f"</div>"
            f'<p class="activity-text"><strong>{title}</strong> &mdash; {words} words ({status})</p>'
            f"</div></div>"
        )

    for r in runs:
        status = r.get("status") or "unknown"
        count = r.get("posts_published") or 0
        raw_ts = str(r.get("started_at") or "")
        ts = _escape(raw_ts[:16])
        if status == "completed" and count > 0:
            parts.append(
                f'<div class="activity-item">'
                f'<div class="activity-dot published"></div>'
                f'<div class="activity-body">'
                f'<div class="activity-header">'
                f'<span class="badge completed">run</span>'
                f'<time datetime="{_escape(raw_ts)}" class="activity-time">{ts}</time>'
                f"</div>"
                f'<p class="activity-text">Created and published {count} post{"s" if count != 1 else ""}</p>'
                f"</div></div>"
            )
        elif status == "failed":
            parts.append(
                f'<div class="activity-item">'
                f'<div class="activity-dot failed"></div>'
                f'<div class="activity-body">'
                f'<div class="activity-header">'
                f'<span class="badge failed">run</span>'
                f'<time datetime="{_escape(raw_ts)}" class="activity-time">{ts}</time>'
                f"</div>"
                f'<p class="activity-text">Content engine encountered an issue</p>'
                f"</div></div>"
            )

    return HTMLResponse("".join(parts[:12]))


@router.get("/api/partials/recent-posts", response_class=HTMLResponse)
async def tenant_recent_posts_partial(request: Request, client: AuthClient):
    """Return the recent posts table as an HTML fragment."""
    db = request.app.state.db
    posts = db.get_recent_posts_with_metrics(limit=20, client_id=client["id"])

    if not posts:
        return HTMLResponse('<p style="opacity: 0.6;">No posts yet.</p>')

    rows = []
    for p in posts:
        text = _escape(p["text"][:80]) + ("..." if len(p["text"]) > 80 else "")
        status = p.get("status", "")
        error_line = ""
        if status == "failed" and p.get("error_message"):
            error_line = f'<br><small style="color: #ef4444;">{_escape(p["error_message"][:120])}</small>'

        badge = _badge("completed" if status == "published" else "running" if status == "draft" else status)
        rows.append(
            f"<tr><td>{text}{error_line}</td>"
            f"<td>{badge}</td>"
            f"<td>{_escape(p.get('platform') or 'generic')}</td>"
            f"<td>{p.get('like_count') or 0}</td>"
            f"<td>{p.get('repost_count') or 0}</td>"
            f"<td>{_escape(str(p.get('published_at') or '-'))}</td></tr>"
        )

    html = (
        "<table><thead><tr>"
        "<th>Text</th><th>Status</th><th>Platform</th><th>Likes</th><th>Reposts</th><th>Published</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )
    return HTMLResponse(html)


@router.get("/api/partials/analytics-kpi", response_class=HTMLResponse)
async def tenant_analytics_kpi_partial(request: Request, client: AuthClient):
    """Return analytics KPI cards as an HTML fragment."""
    db = request.app.state.db
    client_id = client["id"]

    total_row = db.fetchone(
        "SELECT COUNT(*) as count FROM posts WHERE status='published' AND client_id=?",
        (client_id,),
    )
    total_posts = total_row["count"] if total_row else 0

    _MJ = (
        " LEFT JOIN metrics m ON p.id = m.post_id"
        " AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
    )
    platform_rows = db.fetchall(
        "SELECT p.platform, COUNT(DISTINCT p.id) as count,"
        " SUM(COALESCE(m.like_count,0)) as likes,"
        " SUM(COALESCE(m.repost_count,0)) as reposts,"
        " SUM(COALESCE(m.reply_count,0)) as replies"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=?"
        " GROUP BY p.platform",
        (client_id,),
    )

    total_likes = sum(r["likes"] or 0 for r in platform_rows)
    total_reposts = sum(r["reposts"] or 0 for r in platform_rows)
    total_replies = sum(r["replies"] or 0 for r in platform_rows)
    total_engagement = total_likes + total_reposts + total_replies
    avg_engagement = round(total_engagement / total_posts, 1) if total_posts > 0 else 0

    best_platform = "N/A"
    if platform_rows:
        best = max(
            platform_rows,
            key=lambda r: (r["likes"] or 0) + (r["reposts"] or 0) + (r["replies"] or 0),
        )
        best_platform = best["platform"] or "generic"

    html = (
        '<article class="kpi-card">'
        f'<div class="kpi-value">{total_posts}</div>'
        '<div class="kpi-label">Total Posts</div>'
        '<div class="kpi-sublabel">all time published</div></article>'
        '<article class="kpi-card">'
        f'<div class="kpi-value">{total_engagement}</div>'
        '<div class="kpi-label">Total Engagement</div>'
        '<div class="kpi-sublabel">likes + reposts + replies</div></article>'
        '<article class="kpi-card">'
        f'<div class="kpi-value">{avg_engagement}</div>'
        '<div class="kpi-label">Avg Engagement / Post</div>'
        '<div class="kpi-sublabel">across all platforms</div></article>'
        '<article class="kpi-card">'
        f'<div class="kpi-value" style="font-size: 1.8rem;">{_escape(best_platform)}</div>'
        '<div class="kpi-label">Best Platform</div>'
        '<div class="kpi-sublabel">by total engagement</div></article>'
    )
    return HTMLResponse(html)
