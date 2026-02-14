"""Public onboarding API routes for the landing page."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

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

    # Check for duplicate email
    existing = db.conn.execute("SELECT id FROM clients WHERE email=?", (body.email,)).fetchone()
    if existing:
        return JSONResponse(
            {"detail": "An account with this email already exists."},
            status_code=409,
        )

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

    return {"client_id": client_id, "message": "Request received. We'll be in touch soon."}


@router.get("/public/stats")
async def public_stats(request: Request):
    db = request.app.state.db
    stats = db.get_public_stats()
    return stats
