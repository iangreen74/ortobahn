"""Tests for the onboarding API endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ortobahn.web.app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("BLUESKY_HANDLE", "")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.chdir(tmp_path)
    application = create_app()
    cognito = MagicMock()
    cognito.sign_up.return_value = "mock-cognito-sub"
    application.state.cognito = cognito
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestOnboardEndpoint:
    @pytest.mark.asyncio
    async def test_successful_onboard(self, client, app):
        resp = await client.post(
            "/api/onboard",
            json={
                "name": "Jane Smith",
                "company": "AcmeCorp",
                "email": "jane@acme.com",
                "industry": "SaaS",
                "website": "https://acme.com",
                "brand_voice": "Professional",
                "password": "TestPass123",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "client_id" in data
        assert "api_key" in data
        assert data["api_key"].startswith("otb_")
        assert "created" in data["message"].lower()
        assert data["needs_confirmation"] is True

        # Verify trial was initialized
        db = app.state.db
        new_client = db.get_client(data["client_id"])
        assert new_client["subscription_status"] == "trialing"
        assert new_client["trial_ends_at"] is not None

    @pytest.mark.asyncio
    async def test_duplicate_email_rejected(self, client):
        payload = {
            "name": "Jane",
            "company": "AcmeCorp",
            "email": "jane@acme.com",
            "industry": "SaaS",
            "password": "TestPass123",
        }
        await client.post("/api/onboard", json=payload)
        resp = await client.post("/api/onboard", json=payload)
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_missing_required_fields(self, client):
        resp = await client.post(
            "/api/onboard",
            json={
                "name": "Jane",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_email(self, client):
        resp = await client.post(
            "/api/onboard",
            json={
                "name": "Jane",
                "company": "AcmeCorp",
                "email": "not-an-email",
                "industry": "SaaS",
                "password": "TestPass123",
            },
        )
        assert resp.status_code == 422


class TestPublicStatsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_stats(self, client):
        resp = await client.get("/api/public/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_clients" in data
        assert "total_posts_published" in data
        assert "platforms_supported" in data
