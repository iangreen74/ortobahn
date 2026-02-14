"""Tests for the web dashboard."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ortobahn.config import Settings
from ortobahn.db import Database


def _create_test_app(tmp_path):
    """Create a FastAPI app with a test database."""
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "web_test.db",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    # Override with test DB
    app.state.db = Database(settings.db_path)
    app.state.settings = settings
    return app


class TestDashboard:
    def test_index_loads(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Ortobahn" in resp.text
        assert "Dashboard" in resp.text

    def test_index_shows_clients(self, tmp_path):
        app = _create_test_app(tmp_path)
        # Default client should be seeded by migration
        client = TestClient(app)
        resp = client.get("/")
        assert "Ortobahn" in resp.text


class TestClientRoutes:
    def test_client_list(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/clients/")
        assert resp.status_code == 200
        assert "Ortobahn" in resp.text  # Default client

    def test_create_client(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
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
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Verify client was created
        resp = client.get("/clients/")
        assert "TestCorp" in resp.text

    def test_client_detail(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/clients/default")
        assert resp.status_code == 200
        assert "Ortobahn" in resp.text

    def test_nonexistent_client_redirects(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/clients/nonexistent", follow_redirects=False)
        assert resp.status_code == 303


class TestContentRoutes:
    def test_content_list_empty(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/content/")
        assert resp.status_code == 200

    def test_content_with_drafts(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.save_post(text="Test draft", run_id="r1", status="draft", platform="twitter")

        client = TestClient(app)
        resp = client.get("/content/")
        assert "Test draft" in resp.text

    def test_approve_post(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        pid = db.save_post(text="Approve me", run_id="r1", status="draft")

        client = TestClient(app)
        resp = client.post(f"/content/{pid}/approve")
        assert resp.status_code == 200
        assert "approved" in resp.text

        post = db.get_post(pid)
        assert post["status"] == "approved"

    def test_reject_post(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        pid = db.save_post(text="Reject me", run_id="r1", status="draft")

        client = TestClient(app)
        resp = client.post(f"/content/{pid}/reject")
        assert resp.status_code == 200
        assert "rejected" in resp.text

    def test_filter_by_status(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.save_post(text="Draft post", run_id="r1", status="draft")
        db.save_post(text="Approved post", run_id="r1", status="approved")

        client = TestClient(app)
        resp = client.get("/content/?status=draft")
        assert "Draft post" in resp.text


class TestPipelineRoutes:
    def test_pipeline_page_loads(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/pipeline/")
        assert resp.status_code == 200
        assert "Pipeline" in resp.text

    def test_pipeline_shows_runs(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.start_pipeline_run("test-run", mode="single")
        db.complete_pipeline_run("test-run", posts_published=3)

        client = TestClient(app)
        resp = client.get("/pipeline/")
        assert "test-run"[:8] in resp.text
        assert "completed" in resp.text
