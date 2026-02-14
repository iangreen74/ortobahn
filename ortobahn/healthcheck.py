"""Health checks for external dependencies."""

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


def check_config(settings: Settings, require_bluesky: bool = True) -> HealthResult:
    """Validate all configuration values."""
    errors = settings.validate(require_bluesky=require_bluesky)
    if errors:
        return HealthResult("config", False, "; ".join(errors))
    return HealthResult("config", True, "All config values valid")


def check_database(settings: Settings) -> HealthResult:
    """Verify database can be created/opened."""
    try:
        from ortobahn.db import Database

        db = Database(settings.db_path)
        db.get_recent_runs(limit=1)
        db.close()
        return HealthResult("database", True, f"SQLite OK at {settings.db_path}")
    except Exception as e:
        return HealthResult("database", False, f"Database error: {e}")


def check_anthropic(settings: Settings) -> HealthResult:
    """Verify Anthropic API key works."""
    if not settings.anthropic_api_key:
        return HealthResult("anthropic", False, "API key not set")
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        # Minimal API call to verify auth
        client.models.list()
        return HealthResult("anthropic", True, f"API key valid, model {settings.claude_model}")
    except Exception as e:
        return HealthResult("anthropic", False, f"API error: {e}")


def check_bluesky(settings: Settings) -> HealthResult:
    """Verify Bluesky credentials work."""
    if not settings.bluesky_handle or not settings.bluesky_app_password:
        return HealthResult("bluesky", False, "Handle or app password not set")
    try:
        from ortobahn.integrations.bluesky import BlueskyClient

        client = BlueskyClient(settings.bluesky_handle, settings.bluesky_app_password)
        client.login()
        return HealthResult("bluesky", True, f"Logged in as {settings.bluesky_handle}")
    except Exception as e:
        return HealthResult("bluesky", False, f"Login failed: {e}")


def check_twitter(settings: Settings) -> HealthResult:
    """Verify Twitter API credentials work."""
    if not settings.has_twitter():
        return HealthResult("twitter", True, "Not configured (optional)")
    try:
        from ortobahn.integrations.twitter import TwitterClient

        client = TwitterClient(
            settings.twitter_api_key,
            settings.twitter_api_secret,
            settings.twitter_access_token,
            settings.twitter_access_token_secret,
        )
        client._get_client()
        return HealthResult("twitter", True, "Authenticated")
    except Exception as e:
        return HealthResult("twitter", False, f"Auth failed: {e}")


def check_linkedin(settings: Settings) -> HealthResult:
    """Verify LinkedIn API credentials work."""
    if not settings.has_linkedin():
        return HealthResult("linkedin", True, "Not configured (optional)")
    try:
        import requests

        resp = requests.get(
            "https://api.linkedin.com/v2/me",
            headers={"Authorization": f"Bearer {settings.linkedin_access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return HealthResult("linkedin", True, "Authenticated")
    except Exception as e:
        return HealthResult("linkedin", False, f"Auth failed: {e}")


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
