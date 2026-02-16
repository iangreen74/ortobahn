"""Authentication routes: login, API key management."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ortobahn.auth import (
    AdminClient,
    AuthClient,
    create_session_token,
    generate_api_key,
    hash_api_key,
    key_prefix,
)

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    """Render the login page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("login.html", {"request": request})


class LoginRequest(BaseModel):
    api_key: str


@router.post("/login")
async def login(request: Request, body: LoginRequest):
    """Exchange an API key for a JWT session token."""
    db = request.app.state.db
    secret_key = request.app.state.settings.secret_key

    hashed = hash_api_key(body.api_key)
    row = db.conn.execute("SELECT client_id FROM api_keys WHERE key_hash=? AND active=1", (hashed,)).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")

    client = db.get_client(row["client_id"])
    if not client:
        raise HTTPException(status_code=401, detail="Client not found")

    token = create_session_token(row["client_id"], secret_key)
    response = JSONResponse({"token": token, "client_id": row["client_id"], "client_name": client["name"]})
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=86400,
    )
    return response


@router.post("/logout")
async def logout():
    """Clear session cookie."""
    response = JSONResponse({"message": "Logged out"})
    response.delete_cookie("session")
    return response


class CreateApiKeyRequest(BaseModel):
    client_id: str
    name: str = "default"


@router.post("/keys")
async def create_api_key_route(request: Request, body: CreateApiKeyRequest, admin: AdminClient):
    """Create a new API key for a client. Admin only."""
    db = request.app.state.db

    client = db.get_client(body.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)

    db.create_api_key(body.client_id, hashed, prefix, body.name)

    return {"api_key": raw_key, "prefix": prefix, "client_id": body.client_id}


@router.get("/keys")
async def list_api_keys(request: Request, client: AuthClient):
    """List API keys for the authenticated client (shows prefix only)."""
    db = request.app.state.db
    keys = db.get_api_keys_for_client(client["id"])
    return {"keys": keys}
