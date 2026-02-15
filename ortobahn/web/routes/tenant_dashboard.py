"""Tenant dashboard routes -- authenticated self-service views for each client."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ortobahn.auth import AuthClient
from ortobahn.credentials import save_platform_credentials

router = APIRouter(prefix="/my")


@router.get("/dashboard")
async def tenant_dashboard(request: Request, client: AuthClient):
    db = request.app.state.db
    templates = request.app.state.templates

    posts = db.get_recent_posts_with_metrics(limit=20, client_id=client["id"])
    strategy = db.get_active_strategy(client_id=client["id"])
    runs = db.get_recent_runs(limit=5)
    # Filter runs to this client (pipeline_runs have client_id column)
    client_runs = [
        r for r in runs if r.get("client_id") == client["id"]
    ]

    total_published = len([p for p in posts if p.get("status") == "published"])
    total_drafts = len(db.get_drafts_for_review(client_id=client["id"]))

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
        },
    )


@router.get("/settings")
async def tenant_settings(request: Request, client: AuthClient):
    db = request.app.state.db
    templates = request.app.state.templates

    api_keys = db.get_api_keys_for_client(client["id"])

    # Check which platforms have credentials stored
    connected_platforms = []
    for platform in ("bluesky", "twitter", "linkedin"):
        row = db.conn.execute(
            "SELECT id FROM platform_credentials WHERE client_id=? AND platform=?",
            (client["id"], platform),
        ).fetchone()
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
async def tenant_settings_update(
    request: Request,
    client: AuthClient,
    name: str = Form(...),
    industry: str = Form(""),
    target_audience: str = Form(""),
    brand_voice: str = Form(""),
    website: str = Form(""),
    products: str = Form(""),
    competitive_positioning: str = Form(""),
    key_messages: str = Form(""),
    content_pillars: str = Form(""),
    company_story: str = Form(""),
):
    db = request.app.state.db
    db.update_client(
        client["id"],
        {
            "name": name,
            "industry": industry,
            "target_audience": target_audience,
            "brand_voice": brand_voice,
            "website": website,
            "products": products,
            "competitive_positioning": competitive_positioning,
            "key_messages": key_messages,
            "content_pillars": content_pillars,
            "company_story": company_story,
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
