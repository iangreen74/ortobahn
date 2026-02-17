"""Tenant dashboard routes -- authenticated self-service views for each client."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from ortobahn.auth import AuthClient
from ortobahn.credentials import save_platform_credentials
from ortobahn.models import Platform

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
            trial_end = datetime.fromisoformat(client["trial_ends_at"])
            if trial_end.tzinfo is None:
                trial_end = trial_end.replace(tzinfo=timezone.utc)
            delta = trial_end - datetime.now(timezone.utc)
            trial_days_remaining = max(0, delta.days)
        except (ValueError, TypeError):
            pass

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

    return templates.TemplateResponse(
        "tenant_settings.html",
        {
            "request": request,
            "client": client,
            "api_keys": api_keys,
            "connected_platforms": connected_platforms,
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

    save_platform_credentials(db, client["id"], platform, creds, secret_key)
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
