"""Public onboarding API routes for the landing page."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

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

# Map common industries to news categories and keywords for trend personalization.
INDUSTRY_DEFAULTS: dict[str, dict[str, str]] = {
    "saas": {"news_category": "technology", "news_keywords": "SaaS, cloud computing, software"},
    "software": {"news_category": "technology", "news_keywords": "software, SaaS, developer tools"},
    "technology": {"news_category": "technology", "news_keywords": "technology, startups, innovation"},
    "ai": {"news_category": "technology", "news_keywords": "artificial intelligence, machine learning, AI"},
    "fintech": {"news_category": "business", "news_keywords": "fintech, payments, banking, cryptocurrency"},
    "finance": {"news_category": "business", "news_keywords": "finance, investing, markets, wealth management"},
    "healthcare": {"news_category": "health", "news_keywords": "healthcare, medtech, digital health"},
    "health": {"news_category": "health", "news_keywords": "health, wellness, medical technology"},
    "ecommerce": {"news_category": "business", "news_keywords": "ecommerce, retail, DTC, online shopping"},
    "retail": {"news_category": "business", "news_keywords": "retail, consumer goods, shopping"},
    "marketing": {"news_category": "business", "news_keywords": "marketing, advertising, brand strategy"},
    "real estate": {"news_category": "business", "news_keywords": "real estate, proptech, housing market"},
    "education": {"news_category": "science", "news_keywords": "education, edtech, online learning"},
    "cybersecurity": {"news_category": "technology", "news_keywords": "cybersecurity, infosec, data privacy"},
    "crypto": {"news_category": "business", "news_keywords": "cryptocurrency, blockchain, web3, DeFi"},
    "gaming": {"news_category": "entertainment", "news_keywords": "gaming, esports, game development"},
    "media": {"news_category": "entertainment", "news_keywords": "media, content creation, streaming"},
    "legal": {"news_category": "general", "news_keywords": "legal tech, law, compliance, regulation"},
    "consulting": {"news_category": "business", "news_keywords": "consulting, management, strategy"},
    "energy": {"news_category": "science", "news_keywords": "energy, cleantech, renewable, sustainability"},
}


def _match_industry(industry: str) -> dict[str, str]:
    """Match a user-provided industry string to trend defaults using simple keyword matching."""
    industry_lower = industry.lower().strip()
    # Exact match
    if industry_lower in INDUSTRY_DEFAULTS:
        return INDUSTRY_DEFAULTS[industry_lower]
    # Substring match
    for key, defaults in INDUSTRY_DEFAULTS.items():
        if key in industry_lower or industry_lower in key:
            return defaults
    # Default fallback
    return {"news_category": "technology", "news_keywords": ""}


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
    existing = db.fetchone("SELECT id FROM clients WHERE email=?", (body.email,))
    if existing:
        return JSONResponse(
            {"detail": "An account with this email already exists."},
            status_code=409,
        )

    # Match industry to trend defaults
    trend_defaults = _match_industry(body.industry)

    # Create client with pending status and industry-specific trend config
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
    # Set trend config (after create, since create_client has a fixed column set)
    db.update_client(client_id, {
        "news_category": trend_defaults["news_category"],
        "news_keywords": trend_defaults["news_keywords"],
    })

    # Register user in Cognito
    try:
        cognito_sub = request.app.state.cognito.sign_up(
            body.email, body.password, client_id
        )
    except CognitoError as exc:
        log.warning("Cognito sign-up failed for %s: %s", body.email, exc)
        # Rollback: remove the client record we just created
        db.execute("DELETE FROM clients WHERE id=?", (client_id,), commit=True)
        return JSONResponse(
            {"detail": str(exc)},
            status_code=400,
        )

    # Store the Cognito sub and start 14-day free trial
    trial_end = datetime.now(timezone.utc) + timedelta(days=14)
    db.execute(
        "UPDATE clients SET cognito_sub=?, subscription_status='trialing', trial_ends_at=? WHERE id=?",
        (cognito_sub, trial_end.isoformat(), client_id),
        commit=True,
    )

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
