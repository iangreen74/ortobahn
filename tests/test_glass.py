"""Tests for the Glass Company public dashboard."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ortobahn.config import Settings
from ortobahn.db import Database


def _create_test_app(tmp_path):
    """Create a FastAPI app with a test database."""
    from ortobahn.web.app import create_app

    settings = Settings(
        anthropic_api_key="sk-ant-test",
        db_path=tmp_path / "glass_test.db",
        secret_key="test-secret-key-for-glass-tests!",
    )
    app = create_app.__wrapped__() if hasattr(create_app, "__wrapped__") else create_app()
    db = Database(settings.db_path)
    app.state.db = db
    app.state.settings = settings
    app.state.cognito = None
    return app


class TestGlassPage:
    def test_page_loads(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass")
        assert resp.status_code == 200
        assert "Glass Company" in resp.text

    def test_no_auth_required(self, tmp_path):
        """Unlike /, /glass should work without any authentication."""
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass")
        assert resp.status_code == 200

    def test_htmx_attributes_present(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass")
        assert "hx-get" in resp.text
        assert "/glass/api/status" in resp.text


class TestGlassStatus:
    def test_status_endpoint(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass/api/status")
        assert resp.status_code == 200
        assert "glass-pulse" in resp.text

    def test_status_shows_idle_when_no_runs(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass/api/status")
        assert "IDLE" in resp.text

    def test_status_shows_running(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.start_pipeline_run("glass-run-1", mode="single", client_id="default")
        client = TestClient(app)
        resp = client.get("/glass/api/status")
        assert "RUNNING" in resp.text


class TestGlassCosts:
    def test_costs_endpoint(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass/api/costs")
        assert resp.status_code == 200
        assert "$" in resp.text

    def test_costs_with_data(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.start_pipeline_run("cost-run", mode="single", client_id="default")
        db.complete_pipeline_run(
            "cost-run",
            posts_published=1,
            total_input_tokens=100_000,
            total_output_tokens=50_000,
        )
        client = TestClient(app)
        resp = client.get("/glass/api/costs")
        assert "$" in resp.text
        assert "Today" in resp.text
        assert "This month" in resp.text
        assert "All time" in resp.text


class TestGlassHealth:
    def test_health_endpoint(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass/api/health")
        assert resp.status_code == 200
        assert "System Health" in resp.text

    def test_health_shows_success_rate(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.start_pipeline_run("h1", mode="single", client_id="default")
        db.complete_pipeline_run("h1", posts_published=1)
        db.start_pipeline_run("h2", mode="single", client_id="default")
        db.complete_pipeline_run("h2", posts_published=0)
        client = TestClient(app)
        resp = client.get("/glass/api/health")
        assert "100%" in resp.text  # both completed = 100%


class TestGlassAgents:
    def test_agents_endpoint_empty(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass/api/agents")
        assert resp.status_code == 200
        assert "No agent activity" in resp.text

    def test_agents_shows_logs(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.start_pipeline_run("a-run", mode="single", client_id="default")
        db.log_agent(
            "a-run", "ceo",
            output_summary="Set strategy for Q1",
            reasoning="Market analysis shows AI trending",
            input_tokens=5000,
            output_tokens=2000,
            duration_seconds=12.5,
        )
        client = TestClient(app)
        resp = client.get("/glass/api/agents")
        assert "ceo" in resp.text
        assert "Market analysis" in resp.text

    def test_agents_hides_raw_llm_response(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.start_pipeline_run("raw-run", mode="single", client_id="default")
        db.log_agent(
            "raw-run", "creator",
            output_summary="Created post",
            reasoning="topic relevant",
            raw_response="FULL SECRET PROMPT AND RESPONSE DATA",
        )
        client = TestClient(app)
        resp = client.get("/glass/api/agents")
        assert "FULL SECRET PROMPT" not in resp.text
        assert "creator" in resp.text


class TestGlassRuns:
    def test_runs_endpoint_empty(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass/api/runs")
        assert resp.status_code == 200
        assert "No pipeline runs" in resp.text

    def test_runs_shows_data(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.start_pipeline_run("run-123", mode="single", client_id="default")
        db.complete_pipeline_run(
            "run-123",
            posts_published=2,
            total_input_tokens=50000,
            total_output_tokens=10000,
        )
        client = TestClient(app)
        resp = client.get("/glass/api/runs")
        assert "run-123" in resp.text or "run-1234" in resp.text[:8] or "completed" in resp.text

    def test_runs_hides_external_clients(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.create_client({
            "id": "external-corp",
            "name": "Secret Corp",
            "industry": "finance",
            "target_audience": "traders",
            "brand_voice": "formal",
        })
        db.start_pipeline_run("ext-run", mode="single", client_id="external-corp")
        db.complete_pipeline_run("ext-run", posts_published=5)
        client = TestClient(app)
        resp = client.get("/glass/api/runs")
        assert "ext-run" not in resp.text


class TestGlassPosts:
    def test_posts_endpoint_empty(self, tmp_path):
        app = _create_test_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/glass/api/posts")
        assert resp.status_code == 200
        assert "No published posts" in resp.text

    def test_posts_shows_published(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.save_post(
            text="Hello from the glass company!",
            run_id="p-run",
            status="published",
            confidence=0.85,
            client_id="default",
            platform="bluesky",
        )
        client = TestClient(app)
        resp = client.get("/glass/api/posts")
        assert "Hello from the glass company" in resp.text
        assert "85%" in resp.text
        assert "bluesky" in resp.text

    def test_posts_hides_external_clients(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.create_client({
            "id": "secret-client",
            "name": "Secret",
            "industry": "tech",
            "target_audience": "devs",
            "brand_voice": "casual",
        })
        db.save_post(
            text="This is secret external content",
            run_id="s-run",
            status="published",
            confidence=0.9,
            client_id="secret-client",
        )
        client = TestClient(app)
        resp = client.get("/glass/api/posts")
        assert "secret external content" not in resp.text

    def test_posts_confidence_bar(self, tmp_path):
        app = _create_test_app(tmp_path)
        db = app.state.db
        db.save_post(
            text="Test confidence display",
            run_id="c-run",
            status="published",
            confidence=0.42,
            client_id="vaultscaler",
        )
        client = TestClient(app)
        resp = client.get("/glass/api/posts")
        assert "confidence-bar" in resp.text
        assert "42%" in resp.text


class TestGlassRateLimit:
    def test_glass_in_public_tier(self):
        from ortobahn.web.rate_limit import DEFAULT_TIERS, _match_tier

        tier = _match_tier("/glass", DEFAULT_TIERS)
        assert tier is not None
        assert tier.name == "public"

    def test_glass_api_in_public_tier(self):
        from ortobahn.web.rate_limit import DEFAULT_TIERS, _match_tier

        tier = _match_tier("/glass/api/status", DEFAULT_TIERS)
        assert tier is not None
        assert tier.name == "public"
