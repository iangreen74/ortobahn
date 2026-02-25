"""Tenant settings, credentials, and auto-publish routes."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ortobahn.auth import AuthClient
from ortobahn.credentials import save_platform_credentials

logger = logging.getLogger("ortobahn.web.tenant")

router = APIRouter(prefix="/my")


@router.post("/auto-publish")
async def tenant_toggle_auto_publish(
    request: Request,
    client: AuthClient,
):
    """Toggle auto-publish setting for this tenant."""
    db = request.app.state.db
    form = await request.form()
    enabled = 1 if form.get("auto_publish") == "on" else 0
    # Build target_platforms from individual checkboxes
    platforms = []
    for p in ("bluesky", "twitter", "linkedin", "reddit"):
        if form.get(f"platform_{p}"):
            platforms.append(p)
    target_platforms = ",".join(platforms) or "bluesky"
    # Build per-platform schedule JSON
    schedule: dict[str, int] = {}
    for p in platforms:
        val = form.get(f"interval_{p}")
        if val:
            schedule[p] = max(3, min(24, int(str(val))))
        else:
            schedule[p] = 6  # default
    schedule_json = json.dumps(schedule)
    # Global fallback = minimum of all per-platform intervals
    interval = min(schedule.values()) if schedule else 6
    db.execute(
        "UPDATE clients SET auto_publish=?, target_platforms=?, posting_interval_hours=?, platform_schedule=? WHERE id=?",
        (enabled, target_platforms, interval, schedule_json, client["id"]),
        commit=True,
    )
    return RedirectResponse("/my/settings?msg=saved", status_code=303)


@router.get("/settings")
async def tenant_settings(request: Request, client: AuthClient):
    db = request.app.state.db
    templates = request.app.state.templates

    api_keys = db.get_api_keys_for_client(client["id"])

    # Check which platforms have credentials stored
    connected_platforms = []
    for platform in ("bluesky", "twitter", "linkedin", "medium", "substack", "reddit"):
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

    # Parse per-platform schedule from JSON column
    try:
        platform_schedule = json.loads(client.get("platform_schedule") or "{}")
    except (json.JSONDecodeError, TypeError):
        platform_schedule = {}

    return templates.TemplateResponse(
        "tenant_settings.html",
        {
            "request": request,
            "client": client,
            "api_keys": api_keys,
            "connected_platforms": connected_platforms,
            "credential_error": credential_error,
            "platform_schedule": platform_schedule,
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
        # Build article_platforms from individual checkboxes
        platforms = []
        if form.get("article_platform_medium"):
            platforms.append("medium")
        if form.get("article_platform_substack"):
            platforms.append("substack")
        if form.get("article_platform_linkedin"):
            platforms.append("linkedin")
        db.update_client(
            client["id"],
            {
                "article_enabled": 1 if form.get("article_enabled") == "on" else 0,
                "auto_publish_articles": 1 if form.get("auto_publish_articles") == "on" else 0,
                "article_frequency": form.get("article_frequency", "weekly"),
                "article_voice": form.get("article_voice", ""),
                "article_platforms": ",".join(platforms),
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
    return RedirectResponse("/my/settings?msg=saved", status_code=303)


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

    return RedirectResponse("/my/settings?msg=saved", status_code=303)
