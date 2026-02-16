"""Tests for the web dashboard."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ortobahn.auth import generate_api_key, hash_api_key, key_prefix
from ortobahn.config import Settings
from ortobahn.db import Database


def _create_test_app(tmp_path):
    """Create a FastAPI app with a test database."""
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "web_test.db",
        secret_key="test-secret-key-for-web-tests!",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    # Override with test DB
    db = Database(settings.db_path)
    app.state.db = db
    app.state.settings = settings

    # Create an admin API key for the default (internal) client
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)
    db.create_api_key("default", hashed, prefix, "test-admin")
    app.state._test_admin_key = raw_key

    # Mock Cognito client
    mock_cognito = MagicMock()
    mock_cognito.sign_up.return_value = "mock-sub"
    mock_cognito.login.return_value = {"IdToken": "x", "AccessToken": "x", "RefreshToken": "x"}
    mock_cognito.confirm_sign_up.return_value = None
    app.state.cognito = mock_cognito

    return app


def _admin_headers(app):
    """Return headers with admin API key for testing protected routes."""
    return {"X-API-Key": app.state._test_admin_key}


class TestDashboard:
    def test_index_loads(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "Ortobahn" in resp.text
        assert "Dashboard" in resp.text

    def test_index_shows_clients(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/", headers=_admin_headers(app))
        assert "Ortobahn" in resp.text

    def test_unauthenticated_returns_401(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 401


class TestClientRoutes:
    def test_client_list(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/clients/", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "Ortobahn" in resp.text

    def test_create_client(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        headers = _admin_headers(app)
        resp = client.post(
            "/clients/",
            data={
                "name": "TestCorp",
                "description": "A test company",
                "industry": "Testing",
                "target_audience": "QA engineers",
                "brand_voice": "precise",
                "website": "https://testcorp.com",
            },
            headers=headers,
            follow_redirects=False,
        )
        assert resp.status_code == 303

        resp = client.get("/clients/", headers=headers)
        assert "TestCorp" in resp.text

    def test_client_detail(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/clients/default", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "Ortobahn" in resp.text

    def test_nonexistent_client_redirects(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/clients/nonexistent", headers=_admin_headers(app), follow_redirects=False)
        assert resp.status_code == 303


class TestContentRoutes:
    def test_content_list_empty(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/content/", headers=_admin_headers(app))
        assert resp.status_code == 200

    def test_content_with_drafts(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.save_post(text="Test draft", run_id="r1", status="draft", platform="twitter")

        client = TestClient(app)
        resp = client.get("/content/", headers=_admin_headers(app))
        assert "Test draft" in resp.text

    def test_approve_post(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        pid = db.save_post(text="Approve me", run_id="r1", status="draft")

        client = TestClient(app)
        resp = client.post(f"/content/{pid}/approve", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "approved" in resp.text

        post = db.get_post(pid)
        assert post["status"] == "approved"

    def test_reject_post(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        pid = db.save_post(text="Reject me", run_id="r1", status="draft")

        client = TestClient(app)
        resp = client.post(f"/content/{pid}/reject", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "rejected" in resp.text

    def test_filter_by_status(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.save_post(text="Draft post", run_id="r1", status="draft")
        db.save_post(text="Approved post", run_id="r1", status="approved")

        client = TestClient(app)
        resp = client.get("/content/?status=draft", headers=_admin_headers(app))
        assert "Draft post" in resp.text


class TestPipelineRoutes:
    def test_pipeline_page_loads(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/pipeline/", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "Pipeline" in resp.text

    def test_pipeline_shows_runs(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.start_pipeline_run("test-run", mode="single")
        db.complete_pipeline_run("test-run", posts_published=3)

        client = TestClient(app)
        resp = client.get("/pipeline/", headers=_admin_headers(app))
        assert "test-run"[:8] in resp.text
        assert "completed" in resp.text
