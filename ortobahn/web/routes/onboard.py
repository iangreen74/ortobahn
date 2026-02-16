"""Public onboarding API routes for the landing page."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from ortobahn.auth import (
    generate_api_key,
    hash_api_key,
    key_prefix,
)
from ortobahn.cognito import CognitoError

log = logging.getLogger(__name__)

router = APIRouter()


class OnboardRequest(BaseModel):
    name: str
    company: str
    email: EmailStr
    password: str
    industry: str
    website: str = ""
    brand_voice: str = ""


@router.post("/onboard")
async def onboard(request: Request, body: OnboardRequest):
    db = request.app.state.db

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

    # Register user in Cognito
    try:
        cognito_sub = request.app.state.cognito.sign_up(
            body.email, body.password, client_id
        )
    except CognitoError as exc:
        log.warning("Cognito sign-up failed for %s: %s", body.email, exc)
        # Rollback: remove the client record we just created
        db.conn.execute("DELETE FROM clients WHERE id=?", (client_id,))
        db.conn.commit()
        return JSONResponse(
            {"detail": str(exc)},
            status_code=400,
        )

    # Store the Cognito sub on the client record
    db.conn.execute("UPDATE clients SET cognito_sub=? WHERE id=?", (cognito_sub, client_id))
    db.conn.commit()

    # Generate API key for programmatic access
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)
    db.create_api_key(client_id, hashed, prefix, name="default")

    return JSONResponse(
        {
            "client_id": client_id,
            "api_key": raw_key,
            "needs_confirmation": True,
            "message": "Account created! Please verify your email before logging in. "
            "Save your API key -- it cannot be retrieved again.",
        }
    )


@router.get("/public/stats")
async def public_stats(request: Request):
    db = request.app.state.db
    stats = db.get_public_stats()
    return stats
