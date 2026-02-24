"""Tests for the MCP server tools."""

from __future__ import annotations

import pytest

from ortobahn.db import Database


@pytest.fixture
def mcp_db(tmp_path):
    """Fresh SQLite DB for MCP tool testing."""
    db = Database(tmp_path / "mcp_test.db")
    db.create_client(
        {
            "id": "test-client",
            "name": "Test Corp",
            "description": "A test client",
            "industry": "Technology",
            "target_audience": "Developers",
            "brand_voice": "Professional",
        },
        start_trial=False,
    )
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _patch_mcp_db(mcp_db, monkeypatch):
    """Inject test DB into MCP module."""
    import ortobahn.mcp_server as mod

    monkeypatch.setattr(mod, "_db", mcp_db)
    monkeypatch.setattr(mod, "_settings", None)


class TestReadOnlyTools:
    """Tests for read-only MCP tools."""

    def test_list_clients(self):
        from ortobahn.mcp_server import list_clients

        result = list_clients()
        assert "test-client" in result
        assert "Test Corp" in result

    def test_get_client_found(self):
        from ortobahn.mcp_server import get_client

        result = get_client("test-client")
        assert "Test Corp" in result
        assert "Technology" in result

    def test_get_client_not_found(self):
        from ortobahn.mcp_server import get_client

        result = get_client("nonexistent")
        assert "not found" in result

    def test_get_analytics(self):
        from ortobahn.mcp_server import get_analytics

        result = get_analytics(client_id="test-client")
        assert "Total posts" in result

    def test_list_draft_posts_empty(self):
        from ortobahn.mcp_server import list_draft_posts

        result = list_draft_posts(client_id="test-client")
        assert "No draft posts" in result

    def test_list_draft_posts_with_data(self, mcp_db):
        from ortobahn.mcp_server import list_draft_posts

        mcp_db.save_post(
            text="This is a test draft post for MCP",
            run_id="run-1",
            client_id="test-client",
            status="draft",
            confidence=0.85,
        )
        result = list_draft_posts(client_id="test-client")
        assert "test draft post" in result

    def test_get_post_found(self, mcp_db):
        from ortobahn.mcp_server import get_post

        post_id = mcp_db.save_post(
            text="A specific post to retrieve",
            run_id="run-1",
            client_id="test-client",
            status="draft",
            confidence=0.9,
        )
        result = get_post(post_id)
        assert "A specific post to retrieve" in result

    def test_get_post_not_found(self):
        from ortobahn.mcp_server import get_post

        result = get_post("nonexistent-id")
        assert "not found" in result

    def test_get_pipeline_status(self):
        from ortobahn.mcp_server import get_pipeline_status

        result = get_pipeline_status()
        assert "Recent runs" in result or "No pipeline runs" in result

    def test_get_system_health(self):
        from ortobahn.mcp_server import get_system_health

        result = get_system_health()
        assert "System Health" in result

    def test_get_client_strategy_none(self):
        from ortobahn.mcp_server import get_client_strategy

        result = get_client_strategy("test-client")
        assert "No active strategy" in result

    def test_list_articles_empty(self):
        from ortobahn.mcp_server import list_articles

        result = list_articles("test-client")
        assert "No articles" in result

    def test_get_monthly_spend(self):
        from ortobahn.mcp_server import get_monthly_spend

        result = get_monthly_spend("test-client")
        assert "$0" in result

    def test_get_agent_logs_empty(self):
        from ortobahn.mcp_server import get_agent_logs

        result = get_agent_logs()
        assert "No agent logs" in result


class TestWriteTools:
    """Tests for write MCP tools."""

    def test_approve_post_success(self, mcp_db):
        from ortobahn.mcp_server import approve_post

        post_id = mcp_db.save_post(
            text="Draft post to approve",
            run_id="run-1",
            client_id="test-client",
            status="draft",
            confidence=0.85,
        )
        result = approve_post(post_id)
        assert "approved" in result

        # Verify status changed
        post = mcp_db.get_post(post_id)
        assert post["status"] == "approved"

    def test_approve_post_not_draft(self, mcp_db):
        from ortobahn.mcp_server import approve_post

        post_id = mcp_db.save_post(
            text="Already published post",
            run_id="run-1",
            client_id="test-client",
            status="published",
            confidence=0.9,
        )
        result = approve_post(post_id)
        assert "Cannot approve" in result

    def test_approve_post_not_found(self):
        from ortobahn.mcp_server import approve_post

        result = approve_post("nonexistent-id")
        assert "not found" in result

    def test_reject_post_success(self, mcp_db):
        from ortobahn.mcp_server import reject_post

        post_id = mcp_db.save_post(
            text="Draft post to reject",
            run_id="run-1",
            client_id="test-client",
            status="draft",
            confidence=0.85,
        )
        result = reject_post(post_id, reason="Not on brand")
        assert "rejected" in result
        assert "Not on brand" in result

        # Verify status changed
        post = mcp_db.get_post(post_id)
        assert post["status"] == "rejected"

    def test_trigger_pipeline_not_found(self):
        from ortobahn.mcp_server import trigger_pipeline

        result = trigger_pipeline("nonexistent-client")
        assert "not found" in result
