"""Content review and approval routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ortobahn.auth import get_current_client

router = APIRouter(dependencies=[Depends(get_current_client)])


@router.get("/")
async def content_list(request: Request):
    db = request.app.state.db
    templates = request.app.state.templates

    # Get filter params
    client_id = request.query_params.get("client")
    platform = request.query_params.get("platform")
    status = request.query_params.get("status")

    posts = db.get_all_posts(client_id=client_id or None, platform=platform or None, status=status or None)
    clients = db.get_all_clients()

    return templates.TemplateResponse(
        "content.html",
        {
            "request": request,
            "posts": posts,
            "clients": clients,
            "filter_client": client_id or "",
            "filter_platform": platform or "",
            "filter_status": status or "",
        },
    )


@router.post("/{post_id}/approve")
async def approve_post(request: Request, post_id: str):
    db = request.app.state.db
    db.approve_post(post_id)

    # Return updated row for HTMX
    post = db.get_post(post_id)
    if post:
        return HTMLResponse(
            '<span class="badge approved">approved</span>',
        )
    return HTMLResponse("OK")


@router.post("/{post_id}/reject")
async def reject_post(request: Request, post_id: str):
    db = request.app.state.db
    db.reject_post(post_id)

    post = db.get_post(post_id)
    if post:
        return HTMLResponse(
            '<span class="badge rejected">rejected</span>',
        )
    return HTMLResponse("OK")


@router.post("/{post_id}/edit")
async def edit_post(request: Request, post_id: str, text: str = Form(...)):
    db = request.app.state.db
    db.update_post_text(post_id, text)
    return RedirectResponse("/content/", status_code=303)
