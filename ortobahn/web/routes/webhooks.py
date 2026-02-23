"""Webhook management API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

from ortobahn.auth import AuthClient
from ortobahn.webhooks import ALL_EVENTS, delete_webhook, list_webhooks, register_webhook

router = APIRouter(prefix="/api/webhooks")


class WebhookCreate(BaseModel):
    url: HttpUrl
    events: list[str] | None = None


@router.post("")
async def create_webhook(request: Request, body: WebhookCreate, client: AuthClient):
    """Register a new webhook endpoint."""
    db = request.app.state.db

    # Validate event types
    if body.events:
        invalid = [e for e in body.events if e not in ALL_EVENTS]
        if invalid:
            raise HTTPException(400, f"Invalid event types: {invalid}. Valid: {ALL_EVENTS}")

    webhook_id = register_webhook(db, client["id"], str(body.url), body.events)
    webhook = db.fetchone("SELECT id, url, events, secret, created_at FROM webhooks WHERE id=?", (webhook_id,))

    return JSONResponse(
        {
            "id": webhook["id"],
            "url": webhook["url"],
            "events": webhook["events"].split(",") if webhook["events"] != "*" else ["*"],
            "secret": webhook["secret"],
            "created_at": webhook["created_at"],
        },
        status_code=201,
    )


@router.get("")
async def get_webhooks(request: Request, client: AuthClient):
    """List all webhooks for the authenticated client."""
    db = request.app.state.db
    webhooks = list_webhooks(db, client["id"])
    return [
        {
            "id": w["id"],
            "url": w["url"],
            "events": w["events"].split(",") if w["events"] != "*" else ["*"],
            "active": bool(w["active"]),
            "created_at": w["created_at"],
            "last_triggered_at": w["last_triggered_at"],
            "failure_count": w["failure_count"],
        }
        for w in webhooks
    ]


@router.delete("/{webhook_id}")
async def remove_webhook(request: Request, webhook_id: str, client: AuthClient):
    """Delete a webhook."""
    db = request.app.state.db
    delete_webhook(db, webhook_id, client["id"])
    return {"deleted": True}


@router.get("/events")
async def list_events(request: Request):
    """List all available event types."""
    return {"events": ALL_EVENTS}
