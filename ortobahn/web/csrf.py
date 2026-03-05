"""CSRF protection utilities.

Uses a signed double-submit pattern: CSRF token = HMAC(secret_key, session_token).
Token is deterministic per session — no server-side storage needed.
"""

from __future__ import annotations

import hashlib
import hmac


def generate_csrf_token(secret_key: str, session_token: str) -> str:
    """Generate a CSRF token tied to the current session."""
    return hmac.new(
        secret_key.encode(),
        (session_token + ":csrf").encode(),
        hashlib.sha256,
    ).hexdigest()[:40]


def validate_csrf_token(token: str, secret_key: str, session_token: str) -> bool:
    """Validate a CSRF token. Uses constant-time comparison."""
    if not token:
        return False
    expected = generate_csrf_token(secret_key, session_token)
    return hmac.compare_digest(token, expected)
