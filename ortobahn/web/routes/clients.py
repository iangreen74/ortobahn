"""Client management routes."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/")
async def client_list(request: Request):
    db = request.app.state.db
    templates = request.app.state.templates

    clients = db.get_all_clients()
    return templates.TemplateResponse(
        "clients.html",
        {
            "request": request,
            "clients": clients,
        },
    )


@router.post("/")
async def client_create(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    industry: str = Form(""),
    target_audience: str = Form(""),
    brand_voice: str = Form(""),
    website: str = Form(""),
):
    db = request.app.state.db
    db.create_client(
        {
            "name": name,
            "description": description,
            "industry": industry,
            "target_audience": target_audience,
            "brand_voice": brand_voice,
            "website": website,
        }
    )
    return RedirectResponse("/clients/", status_code=303)


@router.get("/{client_id}")
async def client_detail(request: Request, client_id: str):
    db = request.app.state.db
    templates = request.app.state.templates

    client = db.get_client(client_id)
    if not client:
        return RedirectResponse("/clients/", status_code=303)

    strategy = db.get_active_strategy(client_id=client_id)
    posts = db.get_all_posts(client_id=client_id, limit=20)

    return templates.TemplateResponse(
        "client_detail.html",
        {
            "request": request,
            "client": client,
            "strategy": strategy,
            "posts": posts,
        },
    )
