"""Tenant dashboard routes -- authenticated self-service views for each client."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ortobahn.auth import AuthClient
from ortobahn.db import to_datetime

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


@router.get("/dashboard")
async def tenant_dashboard(request: Request, client: AuthClient):
    db = request.app.state.db
    templates = request.app.state.templates

    # Check trial expiry and refresh client data
    if not client.get("internal"):
        sub_status = db.check_and_expire_trial(client["id"])
        if sub_status != client.get("subscription_status"):
            client = db.get_client(client["id"])

    posts = db.get_recent_posts_with_metrics(limit=20, client_id=client["id"])

    total_published = len([p for p in posts if p.get("status") == "published"])
    total_engagement = sum(
        (p.get("like_count") or 0) + (p.get("repost_count") or 0) for p in posts if p.get("status") == "published"
    )

    # Check connected platforms
    connected_platforms = []
    for platform in ("bluesky", "twitter", "linkedin", "medium", "substack", "reddit"):
        row = db.fetchone(
            "SELECT id FROM platform_credentials WHERE client_id=? AND platform=?",
            (client["id"], platform),
        )
        if row:
            connected_platforms.append(platform)

    # Compute trial days remaining
    trial_days_remaining = None
    if client.get("subscription_status") == "trialing" and client.get("trial_ends_at"):
        try:
            trial_end = to_datetime(client["trial_ends_at"])
            if trial_end.tzinfo is None:
                trial_end = trial_end.replace(tzinfo=timezone.utc)
            delta = trial_end - datetime.now(timezone.utc)
            trial_days_remaining = max(0, delta.days)
        except (ValueError, TypeError):
            pass

    credential_issue = client.get("status") == "credential_issue"

    # Time-based greeting
    import time

    hour = int(time.strftime("%H"))
    if hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    # Published today count
    today_cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).isoformat()
    today_row = db.fetchone(
        "SELECT COUNT(*) as c FROM posts WHERE status='published' AND client_id=? AND published_at >= ?",
        (client["id"], today_cutoff),
    )
    published_today = today_row["c"] if today_row else 0

    # Best post (highest engagement)
    _MJ = (
        " LEFT JOIN metrics m ON p.id = m.post_id"
        " AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
    )
    best_post = db.fetchone(
        "SELECT p.id, p.text, p.platform,"
        " COALESCE(m.like_count,0) as like_count,"
        " COALESCE(m.repost_count,0) as repost_count,"
        " COALESCE(m.reply_count,0) as reply_count"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=?"
        " ORDER BY (COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) DESC"
        " LIMIT 1",
        (client["id"],),
    )
    best_post_engagement = 0
    if best_post:
        best_post_engagement = (
            (best_post.get("like_count") or 0)
            + (best_post.get("repost_count") or 0)
            + (best_post.get("reply_count") or 0)
        )

    # Engagement trend (week-over-week percentage change)
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    cutoff_14d = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    this_week_eng = db.fetchone(
        "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=? AND p.published_at >= ?",
        (client["id"], cutoff_7d),
    )
    last_week_eng = db.fetchone(
        "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p"
        + _MJ
        + " WHERE p.status='published' AND p.client_id=? AND p.published_at >= ? AND p.published_at < ?",
        (client["id"], cutoff_14d, cutoff_7d),
    )
    this_avg = (this_week_eng["avg_eng"] or 0) if this_week_eng else 0
    last_avg = (last_week_eng["avg_eng"] or 0) if last_week_eng else 0
    if last_avg > 0:
        engagement_trend_pct = round(((this_avg - last_avg) / last_avg) * 100)
    else:
        engagement_trend_pct = 0

    # Draft count
    draft_row = db.fetchone(
        "SELECT COUNT(*) as c FROM posts WHERE status='draft' AND client_id=?",
        (client["id"],),
    )
    draft_count = draft_row["c"] if draft_row else 0

    # Article count
    article_row = db.fetchone(
        "SELECT COUNT(*) as c FROM articles WHERE client_id=?",
        (client["id"],),
    )
    article_count = article_row["c"] if article_row else 0

    # Voice confidence
    voice_confidence = client.get("voice_confidence") or 0.0
    review_count_row = db.fetchone(
        "SELECT COUNT(*) as c FROM content_reviews WHERE client_id=?",
        (client["id"],),
    )
    total_reviews = review_count_row["c"] if review_count_row else 0

    # Feature flags
    image_gen_enabled = bool(client.get("image_generation_enabled"))
    articles_enabled = bool(client.get("article_enabled"))
    auto_publish = bool(client.get("auto_publish"))

    return templates.TemplateResponse(
        "tenant_dashboard.html",
        {
            "request": request,
            "client": client,
            "total_published": total_published,
            "total_engagement": total_engagement,
            "connected_platforms": connected_platforms,
            "trial_days_remaining": trial_days_remaining,
            "subscription_status": client.get("subscription_status", "none"),
            "credential_issue": credential_issue,
            "greeting": greeting,
            "published_today": published_today,
            "best_post": best_post,
            "best_post_engagement": best_post_engagement,
            "engagement_trend_pct": engagement_trend_pct,
            "draft_count": draft_count,
            "article_count": article_count,
            "voice_confidence": voice_confidence,
            "total_reviews": total_reviews,
            "image_gen_enabled": image_gen_enabled,
            "articles_enabled": articles_enabled,
            "auto_publish": auto_publish,
        },
    )


@router.get("/performance")
async def tenant_performance(request: Request, client: AuthClient):
    """Performance page (renamed from analytics)."""
    return await tenant_analytics(request, client)


@router.get("/analytics")
async def tenant_analytics(request: Request, client: AuthClient):
    """Client-facing analytics dashboard showing content performance."""
    db = request.app.state.db
    templates = request.app.state.templates
    client_id = client["id"]

    # Defensive: if any analytics query fails, show empty state instead of 500
    try:
        return await _render_analytics(db, templates, request, client, client_id)
    except Exception:
        logger.exception("Analytics query failed for client %s", client_id)
        return templates.TemplateResponse(
            "tenant_analytics.html",
            {
                "request": request,
                "client": client,
                "total_posts": 0,
                "total_engagement": 0,
                "avg_engagement": 0,
                "best_platform": "N/A",
                "platform_rows": [],
                "trend_data": [],
                "best_post": None,
                "top_posts": [],
                "recent_posts": [],
            },
        )


async def _render_analytics(db, templates, request, client, client_id):
    # Total posts published (all time)
    total_row = db.fetchone(
        "SELECT COUNT(*) as count FROM posts WHERE status='published' AND client_id=?",
        (client_id,),
    )
    total_posts = total_row["count"] if total_row else 0

    # Common metrics JOIN: posts LEFT JOIN latest metrics snapshot (exactly one row per post)
    _METRICS_JOIN = (
        " LEFT JOIN metrics m ON p.id = m.post_id"
        " AND m.id = (SELECT m2.id FROM metrics m2 WHERE m2.post_id = p.id ORDER BY m2.measured_at DESC LIMIT 1)"
    )

    # Per-platform breakdown: posts count, likes, reposts, replies
    platform_rows = db.fetchall(
        "SELECT p.platform, COUNT(DISTINCT p.id) as count,"
        " SUM(COALESCE(m.like_count,0)) as likes,"
        " SUM(COALESCE(m.repost_count,0)) as reposts,"
        " SUM(COALESCE(m.reply_count,0)) as replies"
        " FROM posts p" + _METRICS_JOIN + " WHERE p.status='published' AND p.client_id=?"
        " GROUP BY p.platform",
        (client_id,),
    )

    # Compute totals from platform breakdown
    total_likes = sum(r["likes"] or 0 for r in platform_rows)
    total_reposts = sum(r["reposts"] or 0 for r in platform_rows)
    total_replies = sum(r["replies"] or 0 for r in platform_rows)
    total_engagement = total_likes + total_reposts + total_replies
    avg_engagement = round(total_engagement / total_posts, 1) if total_posts > 0 else 0

    # Best platform (by total engagement)
    best_platform = "N/A"
    if platform_rows:
        best = max(
            platform_rows,
            key=lambda r: (r["likes"] or 0) + (r["reposts"] or 0) + (r["replies"] or 0),
        )
        best_platform = best["platform"] or "generic"

    # Engagement trend (last 7 days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    trend_rows = db.fetchall(
        "SELECT DATE(p.published_at) as day, COUNT(DISTINCT p.id) as posts,"
        " SUM(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as engagement"
        " FROM posts p" + _METRICS_JOIN + " WHERE p.status='published' AND p.client_id=? AND p.published_at >= ?"
        " GROUP BY DATE(p.published_at) ORDER BY day",
        (client_id, cutoff),
    )

    # Normalize trend for bar chart (percentage of max)
    max_engagement = max((r["engagement"] or 0 for r in trend_rows), default=0)
    trend_data = []
    for r in trend_rows:
        eng = r["engagement"] or 0
        pct = round((eng / max_engagement) * 100) if max_engagement > 0 else 0
        day_label = str(r["day"] or "")[-5:]  # MM-DD
        trend_data.append({"day": day_label, "engagement": eng, "posts": r["posts"], "pct": pct})

    # Best performing post
    best_post = db.fetchone(
        "SELECT p.text, p.platform, COALESCE(m.like_count,0) as like_count,"
        " COALESCE(m.repost_count,0) as repost_count, COALESCE(m.reply_count,0) as reply_count,"
        " p.published_at"
        " FROM posts p" + _METRICS_JOIN + " WHERE p.status='published' AND p.client_id=?"
        " ORDER BY (COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) DESC"
        " LIMIT 1",
        (client_id,),
    )

    # Top 5 posts by engagement
    top_posts = db.fetchall(
        "SELECT p.id, p.text, p.platform, COALESCE(m.like_count,0) as like_count,"
        " COALESCE(m.repost_count,0) as repost_count, COALESCE(m.reply_count,0) as reply_count,"
        " p.published_at"
        " FROM posts p" + _METRICS_JOIN + " WHERE p.status='published' AND p.client_id=?"
        " ORDER BY (COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) DESC"
        " LIMIT 5",
        (client_id,),
    )

    # Recent 10 posts with metrics
    recent_posts = db.fetchall(
        "SELECT p.text, p.platform, COALESCE(m.like_count,0) as like_count,"
        " COALESCE(m.repost_count,0) as repost_count, COALESCE(m.reply_count,0) as reply_count,"
        " p.published_at"
        " FROM posts p" + _METRICS_JOIN + " WHERE p.status='published' AND p.client_id=?"
        " ORDER BY p.published_at DESC"
        " LIMIT 10",
        (client_id,),
    )

    return templates.TemplateResponse(
        "tenant_analytics.html",
        {
            "request": request,
            "client": client,
            "total_posts": total_posts,
            "total_engagement": total_engagement,
            "avg_engagement": avg_engagement,
            "best_platform": best_platform,
            "platform_rows": platform_rows,
            "trend_data": trend_data,
            "best_post": best_post,
            "top_posts": top_posts,
            "recent_posts": recent_posts,
        },
    )


# ---------------------------------------------------------------------------
# Engagement draft review endpoints
# ---------------------------------------------------------------------------


@router.get("/api/partials/engagement-drafts", response_class=HTMLResponse)
async def engagement_drafts(request: Request, client: AuthClient):
    """Return pending engagement reply drafts as HTML fragment."""
    db = request.app.state.db
    client_id = client["id"]

    drafts = db.fetchall(
        "SELECT id, notification_text, reply_text, confidence, platform, created_at"
        " FROM engagement_replies"
        " WHERE client_id=? AND status='draft'"
        " ORDER BY created_at DESC LIMIT 10",
        (client_id,),
    )

    if not drafts:
        return HTMLResponse("")

    import html

    def _esc(s: str) -> str:
        return html.escape(str(s)) if s else ""

    parts = ['<div class="engagement-drafts" style="margin-top: 1rem;">']
    parts.append("<h4>Engagement Drafts</h4>")
    for d in drafts:
        conf = int((d.get("confidence") or 0) * 100)
        parts.append(
            f'<div class="card" style="margin-bottom: 0.5rem; padding: 0.75rem;">'
            f'<p style="opacity: 0.7; font-size: 0.85rem;">Replying to: {_esc((d.get("notification_text") or "")[:100])}</p>'
            f"<p><strong>{_esc(d.get('reply_text') or '')}</strong></p>"
            f'<div style="display: flex; gap: 0.5rem; align-items: center;">'
            f'<span class="badge draft">{_esc(d.get("platform") or "bluesky")}</span>'
            f'<span style="font-size: 0.8rem;">{conf}% confidence</span>'
            f'<form method="post" action="/my/engagement/{d["id"]}/approve" style="margin: 0;">'
            f'<button type="submit" class="outline" style="padding: 0.2rem 0.5rem; font-size: 0.8rem;">Approve</button>'
            f"</form>"
            f'<form method="post" action="/my/engagement/{d["id"]}/reject" style="margin: 0;">'
            f'<button type="submit" class="outline secondary" style="padding: 0.2rem 0.5rem; font-size: 0.8rem;">Reject</button>'
            f"</form>"
            f"</div></div>"
        )
    parts.append("</div>")
    return HTMLResponse("".join(parts))


@router.post("/engagement/{reply_id}/approve")
async def approve_engagement_reply(request: Request, reply_id: str, client: AuthClient):
    """Approve and post a drafted engagement reply."""
    db = request.app.state.db
    client_id = client["id"]

    row = db.fetchone(
        "SELECT id, status FROM engagement_replies WHERE id=? AND client_id=?",
        (reply_id, client_id),
    )
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if row["status"] != "draft":
        return JSONResponse({"error": "Reply is not a draft"}, status_code=400)

    db.execute(
        "UPDATE engagement_replies SET status='posted' WHERE id=? AND client_id=?",
        (reply_id, client_id),
        commit=True,
    )
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/my/dashboard", status_code=303)


@router.post("/engagement/{reply_id}/reject")
async def reject_engagement_reply(request: Request, reply_id: str, client: AuthClient):
    """Reject a drafted engagement reply."""
    db = request.app.state.db
    client_id = client["id"]

    row = db.fetchone(
        "SELECT id, status FROM engagement_replies WHERE id=? AND client_id=?",
        (reply_id, client_id),
    )
    if not row:
        return JSONResponse({"error": "Not found"}, status_code=404)

    db.execute(
        "UPDATE engagement_replies SET status='rejected' WHERE id=? AND client_id=?",
        (reply_id, client_id),
        commit=True,
    )
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/my/dashboard", status_code=303)
