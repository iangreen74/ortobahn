"""Public onboarding API routes for the landing page."""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

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
    name: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    industry: str = Field(min_length=1, max_length=100)
    website: str = Field(default="", max_length=500)
    brand_voice: str = Field(default="", max_length=500)

    @field_validator("website")
    @classmethod
    def validate_website(cls, v: str) -> str:
        if not v:
            return v
        return _validate_url(v)


def _is_internal_hostname(hostname: str) -> bool:
    """Return True if hostname resolves to localhost or a private/reserved IP range."""
    if not hostname:
        return True
    # Block obvious local hostnames
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"):  # noqa: S104
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local
    except ValueError:
        # Not a raw IP — check for localhost-like domains
        return hostname.endswith(".local") or hostname.endswith(".internal")


def _validate_url(url: str) -> str:
    """Validate and normalize a URL, rejecting malformed, localhost, and internal IPs."""
    url = url.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise ValueError(f"Malformed URL: {exc}") from None
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must use http or https scheme")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    if _is_internal_hostname(parsed.hostname):
        raise ValueError("URLs pointing to localhost or internal networks are not allowed")
    if "." not in parsed.hostname:
        raise ValueError("URL hostname must contain a dot (e.g. example.com)")
    return url


def _normalize_url(url: str) -> str:
    """Accept bare domains like acme.com and prepend https://."""
    url = url.strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


@router.post("/onboard")
async def onboard(request: Request, body: OnboardRequest):
    db = request.app.state.db
    body.website = _normalize_url(body.website)

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
    db.update_client(
        client_id,
        {
            "news_category": trend_defaults["news_category"],
            "news_keywords": trend_defaults["news_keywords"],
        },
    )

    # Register user in Cognito
    try:
        cognito_sub = request.app.state.cognito.sign_up(body.email, body.password, client_id)
    except CognitoError as exc:
        log.warning("Cognito sign-up failed for %s: %s", body.email, exc)
        # Rollback: remove the client record we just created
        db.execute("DELETE FROM clients WHERE id=?", (client_id,), commit=True)
        return JSONResponse(
            {"detail": str(exc)},
            status_code=400,
        )

    # Store the Cognito sub (trial already started by create_client)
    db.execute(
        "UPDATE clients SET cognito_sub=? WHERE id=?",
        (cognito_sub, client_id),
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
