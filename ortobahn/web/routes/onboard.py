"""Public onboarding API routes for the landing page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from ortobahn.auth import (
    create_session_token,
    generate_api_key,
    hash_api_key,
    key_prefix,
)

router = APIRouter()


class OnboardRequest(BaseModel):
    name: str
    company: str
    email: EmailStr
    industry: str
    website: str = ""
    brand_voice: str = ""


@router.post("/onboard")
async def onboard(request: Request, body: OnboardRequest):
    db = request.app.state.db
    secret_key = request.app.state.settings.secret_key

    # Check for duplicate email
    existing = db.conn.execute("SELECT id FROM clients WHERE email=?", (body.email,)).fetchone()
    if existing:
        return JSONResponse(
            {"detail": "An account with this email already exists."},
            status_code=409,
        )

    # Create client with pending status
    client_id = db.create_client(
        {
            "name": body.company,
            "description": f"Onboarded via landing page by {body.name}",
            "industry": body.industry,
            "brand_voice": body.brand_voice,
            "website": body.website,
            "email": body.email,
            "status": "pending",
        }
    )

    # Generate API key
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)
    db.create_api_key(client_id, hashed, prefix, name="default")

    # Create session token
    session_token = create_session_token(client_id, secret_key)

    response = JSONResponse(
        {
            "client_id": client_id,
            "api_key": raw_key,
            "session_token": session_token,
            "message": "Account created! Save your API key -- it cannot be retrieved again.",
        }
    )
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=86400,
    )
    return response


@router.get("/public/stats")
async def public_stats(request: Request):
    db = request.app.state.db
    stats = db.get_public_stats()
    return stats
