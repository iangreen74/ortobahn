"""Tests for unified authorization middleware and rate limiting."""

import jwt
import pytest
from fastapi import Security
from fastapi.testclient import TestClient

from ortobahn.web.app import (
    API_KEYS,
    JWT_ALGORITHM,
    JWT_SECRET,
    AuthContext,
    app,
    limiter,
    require_permission,
    require_role,
    unified_auth,
)


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def valid_api_key():
    """Get a valid API key for testing."""
    key = "test-api-key-123"
    API_KEYS.add(key)
    yield key
    API_KEYS.discard(key)


@pytest.fixture
def valid_jwt_token():
    """Create a valid JWT token for testing."""
    payload = {
        "sub": "user123",
        "roles": ["admin"],
        "permissions": ["read", "write"],
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def test_auth_context():
    """Test AuthContext class."""
    ctx = AuthContext(user_id="user1", auth_type="jwt", roles=["admin"], permissions=["read", "write"])
    assert ctx.user_id == "user1"
    assert ctx.has_role("admin")
    assert not ctx.has_role("user")
    assert ctx.has_permission("read")
    assert not ctx.has_permission("delete")


def test_protected_endpoint_with_api_key(client, valid_api_key):
    """Test accessing protected endpoint with API key."""
    @app.get("/test-api-key")
    @limiter.limit("100/minute")
    async def test_endpoint(auth: AuthContext = Security(unified_auth)):
        return {"user_id": auth.user_id, "auth_type": auth.auth_type}

    response = client.get("/test-api-key", headers={"X-API-Key": valid_api_key})
    assert response.status_code == 200
    data = response.json()
    assert data["auth_type"] == "api_key"
    assert "apikey:" in data["user_id"]


def test_protected_endpoint_with_jwt(client, valid_jwt_token):
    """Test accessing protected endpoint with JWT."""
    @app.get("/test-jwt")
    @limiter.limit("100/minute")
    async def test_jwt_endpoint(auth: AuthContext = Security(unified_auth)):
        return {"user_id": auth.user_id, "auth_type": auth.auth_type}

    response = client.get("/test-jwt", headers={"Authorization": f"Bearer {valid_jwt_token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["auth_type"] == "jwt"
    assert data["user_id"] == "user123"


def test_protected_endpoint_without_auth(client):
    """Test accessing protected endpoint without authentication."""
    @app.get("/test-no-auth")
    @limiter.limit("100/minute")
    async def test_no_auth_endpoint(auth: AuthContext = Security(unified_auth)):
        return {"status": "ok"}

    response = client.get("/test-no-auth")
    assert response.status_code == 401


def test_require_role_decorator(client, valid_jwt_token):
    """Test role-based access control decorator."""
    @app.get("/test-role")
    @limiter.limit("100/minute")
    @require_role("admin")
    async def test_role_endpoint(auth: AuthContext):
        return {"status": "ok"}

    response = client.get("/test-role", headers={"Authorization": f"Bearer {valid_jwt_token}"})
    assert response.status_code == 200


def test_require_permission_decorator(client, valid_jwt_token):
    """Test permission-based access control decorator."""
    @app.get("/test-permission")
    @limiter.limit("100/minute")
    @require_permission("read")
    async def test_permission_endpoint(auth: AuthContext):
        return {"status": "ok"}

    response = client.get("/test-permission", headers={"Authorization": f"Bearer {valid_jwt_token}"})
    assert response.status_code == 200


def test_missing_permission(client, valid_jwt_token):
    """Test access denial with missing permission."""
    @app.get("/test-missing-perm")
    @limiter.limit("100/minute")
    @require_permission("admin_delete")
    async def test_missing_perm_endpoint(auth: AuthContext):
        return {"status": "ok"}

    response = client.get("/test-missing-perm", headers={"Authorization": f"Bearer {valid_jwt_token}"})
    assert response.status_code == 403
