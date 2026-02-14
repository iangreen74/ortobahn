"""Dashboard route - main overview page."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/")
async def index(request: Request):
    db = request.app.state.db
    templates = request.app.state.templates

    clients = db.get_all_clients()
    recent_runs = db.get_recent_runs(limit=5)
    pending_drafts = db.get_drafts_for_review()
    strategy = db.get_active_strategy()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "clients": clients,
            "recent_runs": recent_runs,
            "pending_drafts_count": len(pending_drafts),
            "strategy": strategy,
        },
    )
