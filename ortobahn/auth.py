"""Authentication: API key generation, hashing, JWT sessions, and FastAPI dependencies."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPBearer

from ortobahn.db import Database


class _LoginRedirect(Exception):
    """Raised when an unauthenticated browser request needs to be redirected to login."""

    def __init__(self, next_url: str = "/my/dashboard"):
        self.next_url = next_url


# --- API Key utilities ---


def generate_api_key() -> str:
    """Generate a new API key. Format: otb_<40 hex chars>."""
    return f"otb_{secrets.token_hex(20)}"


def hash_api_key(key: str) -> str:
    """SHA-256 hash of an API key for storage."""
    return hashlib.sha256(key.encode()).hexdigest()


def key_prefix(key: str) -> str:
    """First 12 chars of the key for display (otb_xxxx...)."""
    return key[:12]


# --- JWT session utilities ---


def create_session_token(client_id: str, secret_key: str, expires_hours: int = 24) -> str:
    """Create a JWT session token for web dashboard auth."""
    payload = {
        "sub": client_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=expires_hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


def decode_session_token(token: str, secret_key: str) -> str | None:
    """Decode a JWT and return the client_id, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        return payload.get("sub")
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# --- FastAPI dependency functions ---

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_client(
    request: Request,
    api_key: str | None = Security(api_key_header),  # noqa: B008
    bearer=Security(bearer_scheme),  # noqa: B008
) -> dict:
    """Resolve the authenticated client from API key, Bearer JWT, or session cookie.

    Raises 401 if no valid auth found.
    """
    db: Database = request.app.state.db
    secret_key: str = request.app.state.settings.secret_key

    # 1. API Key header
    if api_key:
        hashed = hash_api_key(api_key)
        row = db.conn.execute("SELECT client_id FROM api_keys WHERE key_hash=? AND active=1", (hashed,)).fetchone()
        if row:
            db.conn.execute(
                "UPDATE api_keys SET last_used_at=? WHERE key_hash=?",
                (datetime.now(timezone.utc).isoformat(), hashed),
            )
            db.conn.commit()
            client = db.get_client(row["client_id"])
            if client:
                return client
        raise HTTPException(status_code=401, detail="Invalid API key")

    # 2. Bearer JWT
    if bearer and bearer.credentials:
        client_id = decode_session_token(bearer.credentials, secret_key)
        if client_id:
            client = db.get_client(client_id)
            if client:
                return client
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # 3. Session cookie
    session_token = request.cookies.get("session")
    if session_token:
        client_id = decode_session_token(session_token, secret_key)
        if client_id:
            client = db.get_client(client_id)
            if client:
                return client

    # Redirect browsers to login page; return JSON 401 for API clients
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        raise _LoginRedirect(next_url=request.url.path)
    raise HTTPException(status_code=401, detail="Authentication required")


# Type aliases for route injection
AuthClient = Annotated[dict, Depends(get_current_client)]


async def get_admin_client(client: AuthClient) -> dict:
    """Require the authenticated client to be internal (admin)."""
    if not client.get("internal"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return client


AdminClient = Annotated[dict, Depends(get_admin_client)]
