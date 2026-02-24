"""Health check functions for validating platform credentials and connectivity."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ortobahn.config import Settings

logger = logging.getLogger("ortobahn.healthcheck")


@dataclass
class HealthResult:
    name: str
    ok: bool
    message: str


def health():
    return "ok"


def check_config(settings: Settings, require_bluesky: bool = True) -> HealthResult:
    """Validate core configuration settings."""
    issues: list[str] = []
    if not settings.anthropic_api_key:
        issues.append("ANTHROPIC_API_KEY not set")
    if require_bluesky:
        if not settings.bluesky_handle:
            issues.append("BLUESKY_HANDLE not set")
        if not settings.bluesky_app_password:
            issues.append("BLUESKY_APP_PASSWORD not set")
    if issues:
        return HealthResult("config", False, "; ".join(issues))
    return HealthResult("config", True, "Configuration valid")


def check_database(settings: Settings) -> HealthResult:
    """Verify database connectivity."""
    try:
        from ortobahn.db import create_database

        db = create_database(settings)
        db.close()
        return HealthResult("database", True, "Database connection OK")
    except Exception as exc:
        return HealthResult("database", False, f"Database error: {exc}")


def check_anthropic(settings: Settings) -> HealthResult:
    """Verify Anthropic API key is valid."""
    if not settings.anthropic_api_key:
        return HealthResult("anthropic", False, "API key not set")
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        client.models.list()
        return HealthResult("anthropic", True, "Anthropic API OK")
    except Exception as exc:
        return HealthResult("anthropic", False, f"Anthropic API error: {exc}")


def check_bluesky(settings: Settings) -> HealthResult:
    """Verify Bluesky credentials."""
    if not settings.bluesky_handle or not settings.bluesky_app_password:
        return HealthResult("bluesky", False, "Bluesky credentials not set")
    try:
        from ortobahn.integrations.bluesky import BlueskyClient

        client = BlueskyClient(settings.bluesky_handle, settings.bluesky_app_password)
        client.login()
        return HealthResult("bluesky", True, f"Bluesky authenticated as {settings.bluesky_handle}")
    except Exception as exc:
        return HealthResult("bluesky", False, f"Bluesky login failed: {exc}")


def check_twitter(settings: Settings) -> HealthResult:
    """Verify Twitter credentials."""
    if not settings.has_twitter():
        return HealthResult("twitter", True, "Twitter not configured")
    try:
        from ortobahn.integrations.twitter import TwitterClient

        client = TwitterClient(
            api_key=settings.twitter_api_key,
            api_secret=settings.twitter_api_secret,
            access_token=settings.twitter_access_token,
            access_token_secret=settings.twitter_access_token_secret,
        )
        client._get_client()
        return HealthResult("twitter", True, "Twitter authenticated")
    except Exception as exc:
        return HealthResult("twitter", False, f"Twitter auth failed: {exc}")


def check_linkedin(settings: Settings) -> HealthResult:
    """Verify LinkedIn credentials."""
    if not settings.has_linkedin():
        return HealthResult("linkedin", True, "LinkedIn not configured")
    try:
        import requests

        resp = requests.get(
            "https://api.linkedin.com/v2/me",
            headers={"Authorization": f"Bearer {settings.linkedin_access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return HealthResult("linkedin", True, "LinkedIn authenticated")
    except Exception as exc:
        return HealthResult("linkedin", False, f"LinkedIn auth failed: {exc}")


def run_all_checks(settings: Settings) -> list[HealthResult]:
    """Run all health checks and return results."""
    return [
        check_config(settings),
        check_database(settings),
        check_anthropic(settings),
        check_bluesky(settings),
        check_twitter(settings),
        check_linkedin(settings),
    ]
