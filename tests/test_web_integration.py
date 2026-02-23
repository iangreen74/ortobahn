"""Integration tests for the web middleware chain.

These tests verify that all middleware (rate limiting, CORS, security headers,
access logging) work together correctly. Catches issues like the rate limiter
incident that took down production.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ortobahn.config import Settings
from ortobahn.db import Database


def _create_test_app(tmp_path):
    """Create a test app with a temporary SQLite database."""
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "integration_test.db",
        secret_key="test-secret-key-for-integration",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    db = Database(settings.db_path)
    app.state.db = db
    app.state.settings = settings
    return app


class TestMiddlewareChain:
    """Test that all middleware work together without breaking routes."""

    def test_health_through_middleware(self, tmp_path):
        """Health endpoint must work through all middleware layers."""
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_toasts_through_middleware(self, tmp_path):
        """Toasts API must work through all middleware (caught rate limiter bug)."""
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/api/toasts")
        assert resp.status_code == 200

    def test_root_redirect(self, tmp_path):
        """Root must redirect to tenant dashboard."""
        app = _create_test_app(tmp_path)
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/")
        assert resp.status_code == 302
        assert "/my/dashboard" in resp.headers["location"]

    def test_tenant_dashboard_requires_auth(self, tmp_path):
        """Tenant dashboard must return auth challenge, not crash."""
        app = _create_test_app(tmp_path)
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/my/dashboard")
        # 302 (redirect to login) or 401 (auth required) -- both valid
        assert resp.status_code in (302, 401)

    def test_security_headers_present(self, tmp_path):
        """Security headers must be set on all responses."""
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "strict-origin" in resp.headers.get("Referrer-Policy", "")

    def test_static_files_accessible(self, tmp_path):
        """Static CSS file must be accessible through middleware."""
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "")

    def test_glass_dashboard_public(self, tmp_path):
        """Glass dashboard is public and must work without auth."""
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass")
        assert resp.status_code == 200

    def test_rapid_requests_not_blocked(self, tmp_path):
        """Multiple rapid requests should not be rate limited in tests."""
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        for _ in range(10):
            resp = client.get("/health")
            assert resp.status_code == 200
