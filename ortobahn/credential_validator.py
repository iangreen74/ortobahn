"""Credential validation — test platform API connections per-tenant."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ortobahn.credentials import get_platform_credentials
from ortobahn.db import Database

logger = logging.getLogger("ortobahn.credential_validator")

VALID_PLATFORMS = {"bluesky", "twitter", "linkedin", "reddit", "medium", "substack"}


def validate_credentials(db: Database, client_id: str, platform: str, secret_key: str) -> dict:
    """Test platform credentials and return status.

    Returns: {"status": "valid"|"invalid"|"error", "message": str}
    Updates platform_credentials row with result.
    """
    if platform not in VALID_PLATFORMS:
        return {"status": "error", "message": f"Unknown platform: {platform}"}

    creds = get_platform_credentials(db, client_id, platform, secret_key)
    if not creds:
        result = {"status": "invalid", "message": "No credentials saved"}
        _save_status(db, client_id, platform, result)
        return result

    try:
        result = _test_platform(platform, creds)
    except Exception as e:
        logger.warning(f"Credential validation error for {client_id}/{platform}: {e}")
        result = {"status": "error", "message": str(e)[:200]}

    _save_status(db, client_id, platform, result)
    return result


def _save_status(db: Database, client_id: str, platform: str, result: dict) -> None:
    """Persist validation result to platform_credentials."""
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE platform_credentials SET credential_status=?, credential_status_message=?, "
        "credential_tested_at=? WHERE client_id=? AND platform=?",
        (result["status"], result.get("message", ""), now, client_id, platform),
        commit=True,
    )


def _test_platform(platform: str, creds: dict) -> dict:
    """Test credentials for a specific platform. Returns status dict."""
    func_name = f"_test_{platform}"
    tester = globals().get(func_name)
    if not tester:
        return {"status": "error", "message": f"No validator for {platform}"}
    return tester(creds)


def _test_bluesky(creds: dict) -> dict:
    handle = creds.get("handle", "")
    app_password = creds.get("app_password", "")
    if not handle or not app_password:
        return {"status": "invalid", "message": "Handle and app password required"}
    from ortobahn.integrations.bluesky import BlueskyClient

    client = BlueskyClient(handle, app_password)
    client.login()
    return {"status": "valid", "message": f"Logged in as {handle}"}


def _test_twitter(creds: dict) -> dict:
    required = ("api_key", "api_secret", "access_token", "access_token_secret")
    missing = [k for k in required if not creds.get(k)]
    if missing:
        return {"status": "invalid", "message": f"Missing: {', '.join(missing)}"}
    from ortobahn.integrations.twitter import TwitterClient

    client = TwitterClient(
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        access_token=creds["access_token"],
        access_token_secret=creds["access_token_secret"],
    )
    client._get_client()
    return {"status": "valid", "message": "Twitter credentials verified"}


def _test_linkedin(creds: dict) -> dict:
    token = creds.get("access_token", "")
    urn = creds.get("person_urn", "")
    if not token or not urn:
        return {"status": "invalid", "message": "Access token and person URN required"}
    from ortobahn.integrations.linkedin import LinkedInClient

    client = LinkedInClient(access_token=token, person_urn=urn)
    profile = client.get_profile()
    if profile:
        name = profile.get("first_name", "") or profile.get("vanity_name", "")
        return {"status": "valid", "message": f"Connected as {name}".strip()}
    return {"status": "invalid", "message": "Could not verify profile"}


def _test_reddit(creds: dict) -> dict:
    cid = creds.get("client_id", "")
    secret = creds.get("client_secret", "")
    if not cid or not secret:
        return {"status": "invalid", "message": "Client ID and secret required"}
    from ortobahn.integrations.reddit import RedditClient

    client = RedditClient(
        client_id=cid,
        client_secret=secret,
        username=creds.get("username", ""),
        password=creds.get("password", ""),
        default_subreddit=creds.get("default_subreddit", ""),
    )
    profile = client.get_profile()
    if profile:
        return {"status": "valid", "message": f"Connected as u/{profile.get('username', '')}"}
    return {"status": "invalid", "message": "Could not verify Reddit identity"}


def _test_medium(creds: dict) -> dict:
    token = creds.get("integration_token", "")
    if not token:
        return {"status": "invalid", "message": "Integration token required"}
    from ortobahn.integrations.medium import MediumClient

    client = MediumClient(integration_token=token)
    user_id = client._get_user_id()
    if user_id:
        return {"status": "valid", "message": "Medium token verified"}
    return {"status": "invalid", "message": "Invalid integration token"}


def _test_substack(creds: dict) -> dict:
    subdomain = creds.get("subdomain", "")
    if not subdomain:
        return {"status": "invalid", "message": "Subdomain required"}
    from ortobahn.integrations.substack import SubstackClient

    client = SubstackClient(
        subdomain=subdomain,
        email=creds.get("email", ""),
        password=creds.get("password", ""),
        session_cookie=creds.get("session_cookie", ""),
    )
    client._authenticate()
    return {"status": "valid", "message": f"Connected to {subdomain}.substack.com"}
