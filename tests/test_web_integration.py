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


def _create_authenticated_client(tmp_path):
    """Create a test app with an authenticated session for tenant routes."""
    from ortobahn.auth import create_session_token
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "integration_auth_test.db",
        secret_key="test-secret-key-for-integration",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    db = Database(settings.db_path)
    app.state.db = db
    app.state.settings = settings

    # Create a test client in the DB
    client_id = db.create_client({"name": "IntegrationTestCo"})
    token = create_session_token(client_id, settings.secret_key)

    test_client = TestClient(app)
    test_client.cookies.set("session", token)
    return app, test_client, client_id


class TestAuthenticatedPages:
    """Test that authenticated tenant pages render without 500 errors.

    Prevents regressions like the analytics 500 and article generation
    button not working. These are the pages actual users see.
    """

    def test_analytics_renders_200(self, tmp_path):
        """Analytics page must render, not 500."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/analytics")
        assert resp.status_code == 200
        assert "Analytics" in resp.text

    def test_articles_renders_200(self, tmp_path):
        """Articles page must render, not 500."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/articles")
        assert resp.status_code == 200
        assert "Articles" in resp.text

    def test_settings_renders_200(self, tmp_path):
        """Settings page must render, not 500."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/settings")
        assert resp.status_code == 200
        assert "Settings" in resp.text

    def test_dashboard_renders_200(self, tmp_path):
        """Dashboard page must render, not 500."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/dashboard")
        assert resp.status_code == 200

    def test_generate_article_redirects(self, tmp_path):
        """Generate Article button must POST and redirect, not 500."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.post("/my/generate-article", follow_redirects=False)
        assert resp.status_code == 303
        assert "/my/articles" in resp.headers["location"]
        assert "msg=" in resp.headers["location"]

    def test_calendar_renders_200(self, tmp_path):
        """Calendar page must render, not 500."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/calendar")
        assert resp.status_code == 200
        assert "Calendar" in resp.text

    def test_search_empty_returns_200(self, tmp_path):
        """Search with empty query must return empty 200."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/search")
        assert resp.status_code == 200

    def test_search_with_query_returns_200(self, tmp_path):
        """Search with a query must return 200 with results."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/search?q=settings")
        assert resp.status_code == 200
        assert "Settings" in resp.text

    def test_pipeline_pulse_returns_200(self, tmp_path):
        """Pipeline pulse endpoint must return 200."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/api/pipeline-pulse")
        assert resp.status_code == 200

    def test_review_count_returns_200(self, tmp_path):
        """Review count badge endpoint must return 200."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/api/review-count")
        assert resp.status_code == 200

    def test_review_queue_renders_200(self, tmp_path):
        """Review Queue page must render, not 500."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/review")
        assert resp.status_code == 200
        assert "Review Queue" in resp.text

    def test_posts_page_renders_200(self, tmp_path):
        """Posts page must render, not 500."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/posts")
        assert resp.status_code == 200
        assert "Posts" in resp.text

    def test_activity_page_renders_200(self, tmp_path):
        """Activity page must render, not 500."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/activity")
        assert resp.status_code == 200
        assert "Activity" in resp.text

    def test_performance_page_renders_200(self, tmp_path):
        """Performance page must render (redirects from analytics)."""
        _app, client, _cid = _create_authenticated_client(tmp_path)
        resp = client.get("/my/performance")
        assert resp.status_code == 200
        assert "Analytics" in resp.text
