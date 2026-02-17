"""Encrypted per-tenant platform credential management."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid

from cryptography.fernet import Fernet

from ortobahn.db import Database


def _derive_fernet_key(secret_key: str) -> bytes:
    """Derive a Fernet key from the app secret."""
    digest = hashlib.sha256(secret_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_credentials(creds: dict, secret_key: str) -> str:
    """Encrypt a credentials dict to a Fernet token string."""
    f = Fernet(_derive_fernet_key(secret_key))
    return f.encrypt(json.dumps(creds).encode()).decode()


def decrypt_credentials(encrypted: str, secret_key: str) -> dict:
    """Decrypt a Fernet token back to a credentials dict."""
    f = Fernet(_derive_fernet_key(secret_key))
    return json.loads(f.decrypt(encrypted.encode()))


def save_platform_credentials(db: Database, client_id: str, platform: str, creds: dict, secret_key: str) -> str:
    """Store encrypted credentials for a client+platform. Upserts."""
    encrypted = encrypt_credentials(creds, secret_key)
    existing = db.fetchone(
        "SELECT id FROM platform_credentials WHERE client_id=? AND platform=?",
        (client_id, platform),
    )
    if existing:
        db.execute(
            "UPDATE platform_credentials SET credentials_encrypted=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE client_id=? AND platform=?",
            (encrypted, client_id, platform),
            commit=True,
        )
        return existing["id"]
    cid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO platform_credentials (id, client_id, platform, credentials_encrypted) VALUES (?, ?, ?, ?)",
        (cid, client_id, platform, encrypted),
        commit=True,
    )
    return cid


def get_platform_credentials(db: Database, client_id: str, platform: str, secret_key: str) -> dict | None:
    """Get decrypted credentials for a client+platform, or None."""
    row = db.fetchone(
        "SELECT credentials_encrypted FROM platform_credentials WHERE client_id=? AND platform=?",
        (client_id, platform),
    )
    if row:
        return decrypt_credentials(row["credentials_encrypted"], secret_key)
    return None


def get_all_platform_credentials(db: Database, client_id: str, secret_key: str) -> dict[str, dict]:
    """Get all credentials for a client, keyed by platform name."""
    rows = db.fetchall(
        "SELECT platform, credentials_encrypted FROM platform_credentials WHERE client_id=?",
        (client_id,),
    )
    return {row["platform"]: decrypt_credentials(row["credentials_encrypted"], secret_key) for row in rows}


def build_platform_clients(db: Database, client_id: str, secret_key: str, settings) -> dict:
    """Build platform clients from per-tenant credentials, falling back to global env vars.

    Returns dict: {"bluesky": client|None, "twitter": client|None, "linkedin": client|None}
    """
    from ortobahn.integrations.bluesky import BlueskyClient
    from ortobahn.integrations.linkedin import LinkedInClient
    from ortobahn.integrations.twitter import TwitterClient

    all_creds = get_all_platform_credentials(db, client_id, secret_key)
    clients: dict = {"bluesky": None, "twitter": None, "linkedin": None}

    # Bluesky
    bs_creds = all_creds.get("bluesky")
    if bs_creds and bs_creds.get("handle") and bs_creds.get("app_password"):
        clients["bluesky"] = BlueskyClient(bs_creds["handle"], bs_creds["app_password"])
    elif settings.bluesky_handle and settings.bluesky_app_password:
        clients["bluesky"] = BlueskyClient(settings.bluesky_handle, settings.bluesky_app_password)

    # Twitter
    tw_creds = all_creds.get("twitter")
    if tw_creds and all(tw_creds.get(k) for k in ("api_key", "api_secret", "access_token", "access_token_secret")):
        clients["twitter"] = TwitterClient(
            api_key=tw_creds["api_key"],
            api_secret=tw_creds["api_secret"],
            access_token=tw_creds["access_token"],
            access_token_secret=tw_creds["access_token_secret"],
        )
    elif settings.has_twitter():
        clients["twitter"] = TwitterClient(
            api_key=settings.twitter_api_key,
            api_secret=settings.twitter_api_secret,
            access_token=settings.twitter_access_token,
            access_token_secret=settings.twitter_access_token_secret,
        )

    # LinkedIn
    li_creds = all_creds.get("linkedin")
    if li_creds and li_creds.get("access_token") and li_creds.get("person_urn"):
        clients["linkedin"] = LinkedInClient(
            access_token=li_creds["access_token"],
            person_urn=li_creds["person_urn"],
        )
    elif settings.has_linkedin():
        clients["linkedin"] = LinkedInClient(
            access_token=settings.linkedin_access_token,
            person_urn=settings.linkedin_person_urn,
        )

    return clients
