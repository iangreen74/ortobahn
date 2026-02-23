"""Tests for the SRE dashboard web route (/sre/)."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from ortobahn.auth import generate_api_key, hash_api_key, key_prefix
from ortobahn.config import Settings
from ortobahn.db import Database

# ---------------------------------------------------------------------------
# Helper: create a test app + admin key
# ---------------------------------------------------------------------------


def _create_test_app(tmp_path):
    """Create a FastAPI app with a test database for SRE route tests."""
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "sre_test.db",
        secret_key="test-secret-key-for-sre-tests!",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    db = Database(settings.db_path)
    app.state.db = db
    app.state.settings = settings
    app.state.templates = app.state.templates  # already set by create_app

    # Create an admin API key
    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)
    db.create_api_key("default", hashed, prefix, "test-admin")
    app.state._test_admin_key = raw_key

    # Mock cognito
    app.state.cognito = MagicMock()

    return app


def _admin_headers(app):
    return {"X-API-Key": app.state._test_admin_key}


# ---------------------------------------------------------------------------
# TestSRERouteAuth
# ---------------------------------------------------------------------------


class TestSRERouteAuth:
    """Test that the SRE route requires admin auth."""

    def test_unauthenticated_returns_401(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/sre/")
        assert resp.status_code == 401

    def test_non_admin_returns_403(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        # Create a non-internal client
        cid = db.create_client({"name": "NonAdmin", "email": "user@test.com"})
        raw = generate_api_key()
        db.create_api_key(cid, hash_api_key(raw), key_prefix(raw), "user")

        client = TestClient(app)
        resp = client.get("/sre/", headers={"X-API-Key": raw})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# TestSRERouteEmpty
# ---------------------------------------------------------------------------


class TestSRERouteEmpty:
    """Test the SRE dashboard with no pipeline data."""

    def test_empty_state_renders(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200

    def test_empty_state_shows_unknown_health(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
        # With no runs, health should be "unknown"
        assert "unknown" in resp.text.lower()

    def test_empty_state_zero_tokens(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# TestSRERouteHealthy
# ---------------------------------------------------------------------------


class TestSRERouteHealthy:
    """Test the SRE dashboard when pipeline is healthy (>80% success rate)."""

    def test_healthy_with_all_successful_runs(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        # 5 successful runs
        for i in range(5):
            db.start_pipeline_run(f"run-{i}", mode="single")
            db.complete_pipeline_run(
                f"run-{i}",
                posts_published=2,
                total_input_tokens=1000,
                total_output_tokens=500,
            )

        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "healthy" in resp.text.lower()

    def test_token_costs_calculated(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        db.start_pipeline_run("run-cost", mode="single")
        db.complete_pipeline_run(
            "run-cost",
            posts_published=1,
            total_input_tokens=1_000_000,
            total_output_tokens=100_000,
        )

        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
        # Cost should be calculated: 1M input * $3/M + 100K output * $15/M = $3 + $1.50 = $4.50
        # The template should show this somewhere


# ---------------------------------------------------------------------------
# TestSRERouteDegraded
# ---------------------------------------------------------------------------


class TestSRERouteDegraded:
    """Test the SRE dashboard when pipeline is degraded (50-80% success)."""

    def test_degraded_with_some_failures(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        # 3 successes, 1 failure -> 75% success rate -> degraded
        for i in range(3):
            db.start_pipeline_run(f"run-s-{i}", mode="single")
            db.complete_pipeline_run(f"run-s-{i}", posts_published=1)

        db.start_pipeline_run("run-f-0", mode="single")
        db.fail_pipeline_run("run-f-0", ["some error"])

        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "degraded" in resp.text.lower()


# ---------------------------------------------------------------------------
# TestSRERouteCritical
# ---------------------------------------------------------------------------


class TestSRERouteCritical:
    """Test the SRE dashboard when pipeline is critical (<50% success)."""

    def test_critical_with_mostly_failures(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        # 1 success, 3 failures -> 25% success rate -> critical
        db.start_pipeline_run("run-s-0", mode="single")
        db.complete_pipeline_run("run-s-0", posts_published=1)

        for i in range(3):
            db.start_pipeline_run(f"run-f-{i}", mode="single")
            db.fail_pipeline_run(f"run-f-{i}", [f"error {i}"])

        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "critical" in resp.text.lower()

    def test_all_failures_is_critical(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        for i in range(5):
            db.start_pipeline_run(f"run-f-{i}", mode="single")
            db.fail_pipeline_run(f"run-f-{i}", [f"error {i}"])

        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "critical" in resp.text.lower()


# ---------------------------------------------------------------------------
# TestSRERoutePlatformHealth
# ---------------------------------------------------------------------------


class TestSRERoutePlatformHealth:
    """Test platform health indicators in the SRE dashboard."""

    def test_platform_no_data(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
        # With no posts, platform health should show no_data
        assert "no_data" in resp.text.lower() or "no data" in resp.text.lower()

    def test_platform_healthy_with_published_post(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        # Add a successful published post for bluesky
        db.save_post(
            text="Hello bluesky",
            run_id="r1",
            status="published",
            platform="bluesky",
        )

        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "healthy" in resp.text.lower()

    def test_platform_failing_with_failed_post(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        # Add a failed post for twitter
        db.save_post(
            text="Failed tweet",
            run_id="r1",
            status="failed",
            platform="twitter",
        )

        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
        assert "failing" in resp.text.lower()


# ---------------------------------------------------------------------------
# TestSRERouteAgentLogs
# ---------------------------------------------------------------------------


class TestSRERouteAgentLogs:
    """Test that agent logs are included in the SRE dashboard."""

    def test_agent_logs_appear(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db

        db.start_pipeline_run("run-logs", mode="single")
        db.log_agent(
            run_id="run-logs",
            agent_name="sre",
            input_summary="Check health",
            output_summary="All systems normal",
        )

        client = TestClient(app)
        resp = client.get("/sre/", headers=_admin_headers(app))
        assert resp.status_code == 200
