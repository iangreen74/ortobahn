"""Tenant dashboard routes -- authenticated self-service views for each client."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import stripe
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ortobahn.auth import AuthClient
from ortobahn.credentials import save_platform_credentials
from ortobahn.db import to_datetime
from ortobahn.models import Platform
from ortobahn.web.utils import PIPELINE_STEPS
from ortobahn.web.utils import badge as _badge
from ortobahn.web.utils import escape as _escape
from ortobahn.web.utils import step_index as _step_index

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


def _run_tenant_pipeline(settings, client_id: str, platforms: list[Platform], publish: bool = False):
    """Run pipeline in background for a tenant."""
    from ortobahn.orchestrator import Pipeline

    pipeline = Pipeline(settings, dry_run=not publish)
    try:
        result = pipeline.run_cycle(
            client_id=client_id,
            target_platforms=platforms,
            generate_only=not publish,
        )
        logger.info(f"Tenant pipeline complete for {client_id}: {result['posts_published']} published")
    except Exception as e:
        logger.error(f"Tenant pipeline failed for {client_id}: {e}")
    finally:
        pipeline.close()


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
    strategy = db.get_active_strategy(client_id=client["id"])
    runs = db.get_recent_runs(limit=5)
    # Filter runs to this client (pipeline_runs have client_id column)
    client_runs = [r for r in runs if r.get("client_id") == client["id"]]

    total_published = len([p for p in posts if p.get("status") == "published"])
    total_drafts = len(db.get_drafts_for_review(client_id=client["id"]))

    # Check connected platforms
    connected_platforms = []
    for platform in ("bluesky", "twitter", "linkedin"):
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

    return templates.TemplateResponse(
        "tenant_dashboard.html",
        {
            "request": request,
            "client": client,
            "posts": posts,
            "strategy": strategy,
            "recent_runs": client_runs,
            "total_published": total_published,
            "total_drafts": total_drafts,
            "connected_platforms": connected_platforms,
            "auto_publish": client.get("auto_publish", 0),
            "trial_days_remaining": trial_days_remaining,
            "subscription_status": client.get("subscription_status", "none"),
            "credential_issue": credential_issue,
        },
    )


@router.get("/analytics")
async def tenant_analytics(request: Request, client: AuthClient):
    """Client-facing analytics dashboard showing content performance."""
    db = request.app.state.db
    templates = request.app.state.templates
    client_id = client["id"]

    # Total posts published (all time)
    total_row = db.fetchone(
        "SELECT COUNT(*) as count FROM posts WHERE status='published' AND client_id=?",
        (client_id,),
    )
    total_posts = total_row["count"] if total_row else 0

    # Common metrics JOIN: posts LEFT JOIN latest metrics snapshot
    _METRICS_JOIN = (
        " LEFT JOIN metrics m ON p.id = m.post_id"
        " AND m.measured_at = (SELECT MAX(m2.measured_at) FROM metrics m2 WHERE m2.post_id = p.id)"
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
        " GROUP BY p.id"
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


@router.post("/generate")
async def tenant_generate(
    request: Request,
    background_tasks: BackgroundTasks,
    client: AuthClient,
    platforms: str = Form("bluesky"),
    auto_publish: str = Form(""),
):
    """Trigger a pipeline run for this tenant."""
    settings = request.app.state.settings
    platform_list = [Platform(p.strip()) for p in platforms.split(",") if p.strip()]
    do_publish = auto_publish == "true"

    background_tasks.add_task(_run_tenant_pipeline, settings, client["id"], platform_list, do_publish)

    return RedirectResponse("/my/dashboard", status_code=303)


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


@router.post("/auto-publish")
async def tenant_toggle_auto_publish(
    request: Request,
    client: AuthClient,
    auto_publish: str = Form(""),
    target_platforms: str = Form("bluesky"),
    posting_interval_hours: int = Form(6),
):
    """Toggle auto-publish setting for this tenant."""
    db = request.app.state.db
    enabled = 1 if auto_publish == "on" else 0
    interval = max(3, min(24, posting_interval_hours))
    db.execute(
        "UPDATE clients SET auto_publish=?, target_platforms=?, posting_interval_hours=? WHERE id=?",
        (enabled, target_platforms, interval, client["id"]),
        commit=True,
    )
    return RedirectResponse("/my/settings", status_code=303)


@router.get("/settings")
async def tenant_settings(request: Request, client: AuthClient):
    db = request.app.state.db
    templates = request.app.state.templates

    api_keys = db.get_api_keys_for_client(client["id"])

    # Check which platforms have credentials stored
    connected_platforms = []
    for platform in ("bluesky", "twitter", "linkedin"):
        row = db.fetchone(
            "SELECT id FROM platform_credentials WHERE client_id=? AND platform=?",
            (client["id"], platform),
        )
        if row:
            connected_platforms.append(platform)

    # Check for credential validation errors from redirect
    error_code = request.query_params.get("error")
    credential_error = None
    if error_code == "bluesky_handle_format":
        credential_error = "Bluesky handle should be in the format 'you.bsky.social', not an email address."

    return templates.TemplateResponse(
        "tenant_settings.html",
        {
            "request": request,
            "client": client,
            "api_keys": api_keys,
            "connected_platforms": connected_platforms,
            "credential_error": credential_error,
        },
    )


@router.post("/settings")
async def tenant_settings_update(request: Request, client: AuthClient):
    db = request.app.state.db
    form = await request.form()
    section = form.get("_section", "brand_profile")

    if section == "content_sources":
        db.update_client(
            client["id"],
            {
                "news_category": form.get("news_category", "technology"),
                "news_keywords": form.get("news_keywords", ""),
                "rss_feeds": form.get("rss_feeds", ""),
            },
        )
    elif section == "article_settings":
        db.update_client(
            client["id"],
            {
                "article_enabled": 1 if form.get("article_enabled") == "on" else 0,
                "article_frequency": form.get("article_frequency", "weekly"),
                "article_voice": form.get("article_voice", ""),
                "article_platforms": form.get("article_platforms", ""),
                "article_topics": form.get("article_topics", ""),
                "article_length": form.get("article_length", "medium"),
            },
        )
    else:
        db.update_client(
            client["id"],
            {
                "name": form.get("name", client["name"]),
                "industry": form.get("industry", ""),
                "target_audience": form.get("target_audience", ""),
                "brand_voice": form.get("brand_voice", ""),
                "website": form.get("website", ""),
                "products": form.get("products", ""),
                "competitive_positioning": form.get("competitive_positioning", ""),
                "key_messages": form.get("key_messages", ""),
                "content_pillars": form.get("content_pillars", ""),
                "company_story": form.get("company_story", ""),
            },
        )
    return RedirectResponse("/my/settings", status_code=303)


@router.post("/credentials/{platform}")
async def tenant_save_credentials(
    request: Request,
    platform: str,
    client: AuthClient,
):
    db = request.app.state.db
    secret_key = request.app.state.settings.secret_key

    form = await request.form()
    creds = {k: v for k, v in form.items() if k != "platform" and v}

    # Validate Bluesky handle format
    if platform == "bluesky" and "handle" in creds:
        handle = str(creds["handle"]).strip()
        if "@" in handle or "." not in handle:
            return RedirectResponse("/my/settings?error=bluesky_handle_format", status_code=303)
        creds["handle"] = handle

    save_platform_credentials(db, client["id"], platform, creds, secret_key)

    # Re-activate client if they were paused due to credential issues
    if client.get("status") == "credential_issue" and creds:
        db.update_client(client["id"], {"status": "active"})
        logger.info(f"Client {client['id']} re-activated after credential update")

    return RedirectResponse("/my/settings", status_code=303)


@router.get("/subscribe")
async def tenant_subscribe(request: Request, client: AuthClient):
    """Create a Stripe Checkout session and redirect the user to it."""
    settings = request.app.state.settings
    db = request.app.state.db

    if not settings.stripe_secret_key or not settings.stripe_price_id:
        raise HTTPException(status_code=503, detail="Payments not configured")

    stripe.api_key = settings.stripe_secret_key

    # Get or create Stripe customer
    customer_id = client.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            name=client["name"],
            email=client.get("email", ""),
            metadata={"ortobahn_client_id": client["id"]},
        )
        customer_id = customer.id
        db.update_subscription(client["id"], stripe_customer_id=customer_id)

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": settings.stripe_price_id, "quantity": 1}],
        mode="subscription",
        success_url=str(request.url_for("tenant_dashboard").replace(query="payment=success")),
        cancel_url=str(request.url_for("tenant_dashboard").replace(query="payment=cancelled")),
        metadata={"ortobahn_client_id": client["id"]},
    )

    return RedirectResponse(str(session.url or "/my/dashboard"), status_code=303)


@router.get("/articles")
async def tenant_articles(request: Request, client: AuthClient):
    """List articles with status badges and publication errors."""
    db = request.app.state.db
    templates = request.app.state.templates
    articles = db.get_recent_articles(client["id"], limit=50)
    pubs_by_article: dict = {}
    for a in articles:
        pubs_by_article[a["id"]] = db.get_article_publications(a["id"])
    return templates.TemplateResponse(
        "tenant_articles.html",
        {"request": request, "client": client, "articles": articles, "pubs_by_article": pubs_by_article},
    )


@router.post("/articles/{article_id}/approve")
async def tenant_approve_article(request: Request, article_id: str, client: AuthClient):
    db = request.app.state.db
    article = db.get_article(article_id)
    if not article or article.get("client_id") != client["id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    db.approve_article(article_id)
    return RedirectResponse("/my/articles", status_code=303)


@router.post("/articles/{article_id}/reject")
async def tenant_reject_article(request: Request, article_id: str, client: AuthClient):
    db = request.app.state.db
    article = db.get_article(article_id)
    if not article or article.get("client_id") != client["id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    db.reject_article(article_id)
    return RedirectResponse("/my/articles", status_code=303)


@router.post("/articles/{article_id}/edit")
async def tenant_edit_article(request: Request, article_id: str, client: AuthClient):
    db = request.app.state.db
    article = db.get_article(article_id)
    if not article or article.get("client_id") != client["id"]:
        raise HTTPException(status_code=404, detail="Article not found")
    form = await request.form()
    db.update_article_body(
        article_id,
        title=form.get("title", article["title"]),
        subtitle=form.get("subtitle", article.get("subtitle", "")),
        body_markdown=form.get("body_markdown", article["body_markdown"]),
    )
    return RedirectResponse("/my/articles", status_code=303)


@router.post("/articles/{article_id}/publish")
async def tenant_publish_article(
    request: Request, article_id: str, background_tasks: BackgroundTasks, client: AuthClient
):
    """Approve and publish an article to configured platforms."""
    db = request.app.state.db
    settings = request.app.state.settings
    article = db.get_article(article_id)
    if not article or article.get("client_id") != client["id"]:
        raise HTTPException(status_code=404, detail="Article not found")

    db.approve_article(article_id)

    def _do_publish():
        from ortobahn.orchestrator import Pipeline

        pipeline = Pipeline(settings)
        try:
            pipeline._publish_article(article_id, client["id"])
            pipeline.db.execute(
                "UPDATE articles SET status='published', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (article_id,),
                commit=True,
            )
        except Exception as e:
            logger.error(f"Article publish failed: {e}")
        finally:
            pipeline.close()

    background_tasks.add_task(_do_publish)
    return RedirectResponse("/my/articles", status_code=303)


@router.post("/generate-article")
async def tenant_generate_article(request: Request, background_tasks: BackgroundTasks, client: AuthClient):
    """Trigger one-shot article generation."""
    settings = request.app.state.settings

    def _do_generate():
        from ortobahn.orchestrator import Pipeline

        pipeline = Pipeline(settings)
        try:
            result = pipeline.run_article_cycle(client_id=client["id"])
            logger.info(f"Article generation for {client['id']}: {result['status']}")
        except Exception as e:
            logger.error(f"Article generation failed for {client['id']}: {e}")
        finally:
            pipeline.close()

    background_tasks.add_task(_do_generate)
    return RedirectResponse("/my/articles?msg=generating", status_code=303)


@router.post("/billing")
async def tenant_billing_portal(request: Request, client: AuthClient):
    """Redirect to Stripe Customer Portal for subscription management."""
    settings = request.app.state.settings

    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Payments not configured")

    stripe.api_key = settings.stripe_secret_key
    customer_id = client.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")

    portal_session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=str(request.url_for("tenant_settings")),
    )

    return RedirectResponse(portal_session.url, status_code=303)


# ---------------------------------------------------------------------------
# HTMX fragment endpoints (auto-polled by the tenant dashboard)
# ---------------------------------------------------------------------------


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
        html = (
            '<div class="glass-status-card">'
            '<span class="glass-pulse running"></span>'
            f" <strong>Pipeline running</strong> &mdash; step {step_num}/{total_steps}: {_escape(step_name)}"
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
            html = (
                '<div class="glass-status-card">'
                '<span class="glass-pulse failed"></span>'
                f" <strong>Last run failed</strong> &mdash; {_escape(str(last.get('completed_at') or 'unknown'))}"
                f"{draft_note}"
                "</div>"
            )
        elif last:
            published = last.get("posts_published") or 0
            html = (
                '<div class="glass-status-card">'
                '<span class="glass-pulse idle"></span>'
                f" <strong>Pipeline idle</strong> &mdash; last run published {published} post(s)"
                f"{draft_note}"
                "</div>"
            )
        else:
            html = (
                '<div class="glass-status-card">'
                '<span class="glass-pulse idle"></span>'
                " <strong>Pipeline idle</strong> &mdash; awaiting first run"
                "</div>"
            )

    return HTMLResponse(html)


@router.get("/api/health", response_class=HTMLResponse)
async def tenant_health(request: Request, client: AuthClient):
    """System health stats — polled every 30s by the dashboard."""
    db = request.app.state.db
    cid = client["id"]

    # Posts published in last 24h — the metric that matters
    from datetime import timedelta

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
    total_drafts = len(db.get_drafts_for_review(client_id=client["id"]))
    auto_publish = client.get("auto_publish", 0)
    status = client.get("status") or "active"

    auto_pub_label = "Auto-publish: enabled" if auto_publish else "Auto-publish: off (drafts only)"

    html = (
        "<article><header>Published Posts</header>"
        f"<p><strong>{total_published}</strong></p></article>"
        "<article><header>Drafts Pending</header>"
        f"<p><strong>{total_drafts}</strong></p></article>"
        "<article><header>Status</header>"
        f"<p><strong>{_escape(status)}</strong></p>"
        f"<small>{auto_pub_label}</small></article>"
    )
    return HTMLResponse(html)


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
        " AND m.measured_at = (SELECT MAX(m2.measured_at) FROM metrics m2 WHERE m2.post_id = p.id)"
    )
    platform_rows = db.fetchall(
        "SELECT p.platform, COUNT(*) as count,"
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


# ---------------------------------------------------------------------------
# AI Insights Engine
# ---------------------------------------------------------------------------


def _generate_insights(db, client_id: str) -> list[dict]:
    """Analyze client post data and generate actionable insights.

    Each insight is a dict with keys: icon, title, detail, category.
    Queries the database directly -- no LLM call needed for speed.
    """
    insights: list[dict] = []

    # Metrics live in a separate table; join to get engagement data
    _MJ = (
        " LEFT JOIN metrics m ON p.id = m.post_id"
        " AND m.measured_at = (SELECT MAX(m2.measured_at) FROM metrics m2 WHERE m2.post_id = p.id)"
    )

    # ------------------------------------------------------------------
    # 1. Best posting day analysis
    # ------------------------------------------------------------------
    try:
        day_stats = db.fetchall(
            "SELECT CASE CAST(strftime('%%w', p.published_at) AS INTEGER)"
            "  WHEN 0 THEN 'Sunday' WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday'"
            "  WHEN 3 THEN 'Wednesday' WHEN 4 THEN 'Thursday'"
            "  WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday' END as day_name,"
            " COUNT(*) as cnt,"
            " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
            " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=? AND p.published_at IS NOT NULL"
            " GROUP BY strftime('%%w', p.published_at)"
            " HAVING cnt >= 2"
            " ORDER BY avg_eng DESC",
            (client_id,),
        )
        if len(day_stats) >= 2:
            best_day = day_stats[0]
            worst_day = day_stats[-1]
            best_avg = best_day["avg_eng"] or 0
            worst_avg = worst_day["avg_eng"] or 0
            if worst_avg > 0:
                ratio = best_avg / worst_avg
                if ratio >= 1.5:
                    insights.append(
                        {
                            "icon": "calendar",
                            "title": f"Your posts perform {ratio:.1f}x better on {best_day['day_name']}s",
                            "detail": (
                                f"Average engagement on {best_day['day_name']}: {best_avg:.0f} vs "
                                f"{worst_day['day_name']}: {worst_avg:.0f}. "
                                f"Consider scheduling more content for {best_day['day_name']}."
                            ),
                            "category": "timing",
                        }
                    )
    except Exception:
        pass  # strftime not available on PostgreSQL

    # ------------------------------------------------------------------
    # 2. Best posting hour analysis
    # ------------------------------------------------------------------
    try:
        hour_stats = db.fetchall(
            "SELECT CAST(strftime('%%H', p.published_at) AS INTEGER) as hour,"
            " COUNT(*) as cnt,"
            " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
            " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=? AND p.published_at IS NOT NULL"
            " GROUP BY strftime('%%H', p.published_at)"
            " HAVING cnt >= 2"
            " ORDER BY avg_eng DESC",
            (client_id,),
        )
        if hour_stats:
            top_hours = hour_stats[:3]
            hours_str = ", ".join(f"{h['hour']}:00" for h in top_hours)
            if len(top_hours) >= 2:
                insights.append(
                    {
                        "icon": "clock",
                        "title": f"Your audience is most active around {top_hours[0]['hour']}:00",
                        "detail": (
                            f"Top engagement hours: {hours_str}. "
                            f"Posts at {top_hours[0]['hour']}:00 average "
                            f"{top_hours[0]['avg_eng']:.0f} engagement."
                        ),
                        "category": "timing",
                    }
                )
    except Exception:
        pass  # strftime not available on PostgreSQL

    # ------------------------------------------------------------------
    # 3. Content type / platform comparison
    # ------------------------------------------------------------------
    platform_stats = db.fetchall(
        "SELECT p.platform, COUNT(*) as cnt,"
        " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=?"
        " GROUP BY p.platform HAVING cnt >= 2"
        " ORDER BY avg_eng DESC",
        (client_id,),
    )
    if len(platform_stats) >= 2:
        top_plat = platform_stats[0]
        bottom_plat = platform_stats[-1]
        top_avg = top_plat["avg_eng"] or 0
        bottom_avg = bottom_plat["avg_eng"] or 0
        if bottom_avg > 0:
            plat_ratio = top_avg / bottom_avg
            if plat_ratio >= 1.3:
                insights.append(
                    {
                        "icon": "target",
                        "title": (
                            f"{(top_plat['platform'] or 'generic').capitalize()} "
                            f"outperforms {(bottom_plat['platform'] or 'generic').capitalize()} "
                            f"by {plat_ratio:.1f}x"
                        ),
                        "detail": (
                            f"Average engagement: {top_plat['platform'] or 'generic'} = {top_avg:.0f}, "
                            f"{bottom_plat['platform'] or 'generic'} = {bottom_avg:.0f}. "
                            "Consider focusing more effort on your stronger platform."
                        ),
                        "category": "platform",
                    }
                )

    # ------------------------------------------------------------------
    # 4. Content length insight (short vs long posts)
    # ------------------------------------------------------------------
    short_stats = db.fetchone(
        "SELECT COUNT(*) as cnt,"
        " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=? AND LENGTH(p.text) < 150",
        (client_id,),
    )
    long_stats = db.fetchone(
        "SELECT COUNT(*) as cnt,"
        " AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=? AND LENGTH(p.text) >= 150",
        (client_id,),
    )
    if short_stats and long_stats and (short_stats["cnt"] or 0) >= 2 and (long_stats["cnt"] or 0) >= 2:
        short_avg = short_stats["avg_eng"] or 0
        long_avg = long_stats["avg_eng"] or 0
        if short_avg > 0 and long_avg > 0:
            if short_avg > long_avg * 1.3:
                insights.append(
                    {
                        "icon": "zap",
                        "title": "Shorter posts are winning",
                        "detail": (
                            f"Posts under 150 chars average {short_avg:.0f} engagement vs "
                            f"{long_avg:.0f} for longer posts. Keep it punchy."
                        ),
                        "category": "content",
                    }
                )
            elif long_avg > short_avg * 1.3:
                insights.append(
                    {
                        "icon": "file-text",
                        "title": "Longer posts get more engagement",
                        "detail": (
                            f"Posts over 150 chars average {long_avg:.0f} engagement vs "
                            f"{short_avg:.0f} for shorter posts. Your audience appreciates depth."
                        ),
                        "category": "content",
                    }
                )

    # ------------------------------------------------------------------
    # 5. Posting cadence health
    # ------------------------------------------------------------------
    recent_count_row = db.fetchone(
        "SELECT COUNT(*) as cnt FROM posts WHERE status='published' AND client_id=?"
        " AND published_at >= datetime('now', '-7 days')",
        (client_id,),
    )
    prev_count_row = db.fetchone(
        "SELECT COUNT(*) as cnt FROM posts WHERE status='published' AND client_id=?"
        " AND published_at >= datetime('now', '-14 days')"
        " AND published_at < datetime('now', '-7 days')",
        (client_id,),
    )
    recent_cnt = recent_count_row["cnt"] if recent_count_row else 0
    prev_cnt = prev_count_row["cnt"] if prev_count_row else 0

    if prev_cnt > 0 and recent_cnt > 0:
        change_pct = ((recent_cnt - prev_cnt) / prev_cnt) * 100
        if change_pct >= 30:
            insights.append(
                {
                    "icon": "trending-up",
                    "title": f"Posting volume up {change_pct:.0f}% this week",
                    "detail": (
                        f"{recent_cnt} posts this week vs {prev_cnt} last week. Great momentum -- keep it going!"
                    ),
                    "category": "cadence",
                }
            )
        elif change_pct <= -30:
            insights.append(
                {
                    "icon": "trending-down",
                    "title": f"Posting volume dropped {abs(change_pct):.0f}%",
                    "detail": (
                        f"{recent_cnt} posts this week vs {prev_cnt} last week. "
                        "Consistent posting builds audience growth."
                    ),
                    "category": "cadence",
                }
            )
    elif recent_cnt == 0 and prev_cnt > 0:
        insights.append(
            {
                "icon": "alert-triangle",
                "title": "No posts published this week",
                "detail": (
                    f"You published {prev_cnt} posts last week but none this week. "
                    "Run the pipeline to keep your audience engaged."
                ),
                "category": "cadence",
            }
        )

    # ------------------------------------------------------------------
    # 6. Engagement growth trend
    # ------------------------------------------------------------------
    recent_eng_row = db.fetchone(
        "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=?"
        " AND p.published_at >= datetime('now', '-7 days')",
        (client_id,),
    )
    prev_eng_row = db.fetchone(
        "SELECT AVG(COALESCE(m.like_count,0)+COALESCE(m.repost_count,0)+COALESCE(m.reply_count,0)) as avg_eng"
        " FROM posts p" + _MJ + " WHERE p.status='published' AND p.client_id=?"
        " AND p.published_at >= datetime('now', '-14 days')"
        " AND p.published_at < datetime('now', '-7 days')",
        (client_id,),
    )
    recent_eng = (recent_eng_row["avg_eng"] or 0) if recent_eng_row else 0
    prev_eng = (prev_eng_row["avg_eng"] or 0) if prev_eng_row else 0

    if prev_eng > 0 and recent_eng > 0:
        eng_change = ((recent_eng - prev_eng) / prev_eng) * 100
        if eng_change >= 20:
            insights.append(
                {
                    "icon": "award",
                    "title": f"Engagement up {eng_change:.0f}% week-over-week",
                    "detail": (
                        f"Average engagement rose from {prev_eng:.1f} to {recent_eng:.1f}. "
                        "Your content strategy is resonating."
                    ),
                    "category": "growth",
                }
            )
        elif eng_change <= -20:
            insights.append(
                {
                    "icon": "bar-chart",
                    "title": f"Engagement dipped {abs(eng_change):.0f}% this week",
                    "detail": (
                        f"Average engagement dropped from {prev_eng:.1f} to {recent_eng:.1f}. "
                        "Try varying your content format or posting times."
                    ),
                    "category": "growth",
                }
            )

    # ------------------------------------------------------------------
    # 7. Failure rate warning
    # ------------------------------------------------------------------
    failed_count, total_attempts = db.get_post_failure_rate(hours=168, client_id=client_id)
    if total_attempts >= 3 and failed_count > 0:
        fail_pct = (failed_count / total_attempts) * 100
        if fail_pct >= 25:
            insights.append(
                {
                    "icon": "alert-circle",
                    "title": f"{fail_pct:.0f}% of posts failed to publish this week",
                    "detail": (
                        f"{failed_count} out of {total_attempts} posts failed. "
                        "Check your platform credentials in Settings."
                    ),
                    "category": "health",
                }
            )

    # ------------------------------------------------------------------
    # Fallback when there is not enough data
    # ------------------------------------------------------------------
    if not insights:
        total_row = db.fetchone(
            "SELECT COUNT(*) as cnt FROM posts WHERE status='published' AND client_id=?",
            (client_id,),
        )
        total = total_row["cnt"] if total_row else 0
        if total == 0:
            insights.append(
                {
                    "icon": "rocket",
                    "title": "Publish your first post to unlock insights",
                    "detail": (
                        "Insights are generated from your publishing history. "
                        "Run the pipeline above to generate and publish content, "
                        "then come back for data-driven recommendations."
                    ),
                    "category": "onboarding",
                }
            )
        elif total < 5:
            insights.append(
                {
                    "icon": "bar-chart",
                    "title": f"{total} posts published -- keep going!",
                    "detail": (
                        "We need at least 5 published posts to generate meaningful insights. "
                        f"You're {5 - total} post(s) away from unlocking trend analysis."
                    ),
                    "category": "onboarding",
                }
            )

    return insights[:5]


_INSIGHT_ICONS = {
    "calendar": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
    "clock": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    "target": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
    "zap": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
    "file-text": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    "trending-up": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
    "trending-down": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/></svg>',
    "alert-triangle": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    "alert-circle": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
    "award": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/></svg>',
    "bar-chart": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>',
    "rocket": '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/></svg>',
}

_CATEGORY_COLORS = {
    "timing": "#6366f1",
    "platform": "#00d4aa",
    "content": "#f59e0b",
    "cadence": "#3b82f6",
    "growth": "#10b981",
    "health": "#ef4444",
    "onboarding": "#8b5cf6",
}


@router.get("/api/partials/insights", response_class=HTMLResponse)
async def tenant_insights_partial(request: Request, client: AuthClient):
    """Return AI-generated insights as an HTML fragment."""
    db = request.app.state.db
    try:
        insights = _generate_insights(db, client["id"])
    except Exception:
        insights = []

    if not insights:
        return HTMLResponse('<p style="opacity: 0.6; text-align: center;">Not enough data for insights yet.</p>')

    parts = []
    for insight in insights:
        icon_svg = _INSIGHT_ICONS.get(insight["icon"], _INSIGHT_ICONS["bar-chart"])
        color = _CATEGORY_COLORS.get(insight["category"], "#6366f1")
        parts.append(
            f'<div class="insight-item" style="border-left: 3px solid {color};">'
            f'<div class="insight-header">'
            f'<span class="insight-icon" style="color: {color};">{icon_svg}</span>'
            f"<strong>{_escape(insight['title'])}</strong>"
            f"</div>"
            f'<p class="insight-detail">{_escape(insight["detail"])}</p>'
            f"</div>"
        )

    return HTMLResponse("".join(parts))
