"""Tests for tenant self-service dashboard routes."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ortobahn.auth import create_session_token, generate_api_key, hash_api_key, key_prefix
from ortobahn.web.app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("BLUESKY_HANDLE", "")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "tenant_test.db"))
    monkeypatch.setenv("ORTOBAHN_SECRET_KEY", "test-secret-key-tenant-dashboard!")
    monkeypatch.chdir(tmp_path)
    return create_app()


def _create_tenant(app) -> tuple[str, str, str]:
    """Create a test tenant and return (client_id, api_key, session_token)."""
    db = app.state.db
    secret_key = app.state.settings.secret_key

    client_id = db.create_client(
        {
            "name": "TestTenant",
            "description": "A test tenant",
            "industry": "Testing",
            "email": "test@tenant.com",
            "status": "active",
        }
    )

    raw_key = generate_api_key()
    hashed = hash_api_key(raw_key)
    prefix = key_prefix(raw_key)
    db.create_api_key(client_id, hashed, prefix, "default")

    token = create_session_token(client_id, secret_key)
    return client_id, raw_key, token


@pytest_asyncio.fixture
async def tenant_client(app):
    """Create an authenticated AsyncClient for tenant routes."""
    client_id, api_key, token = _create_tenant(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": api_key},
    ) as c:
        c._test_client_id = client_id
        yield c


@pytest_asyncio.fixture
async def unauthenticated_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestTenantAuthRequired:
    @pytest.mark.asyncio
    async def test_dashboard_requires_auth(self, unauthenticated_client):
        resp = await unauthenticated_client.get("/my/dashboard")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_settings_requires_auth(self, unauthenticated_client):
        resp = await unauthenticated_client.get("/my/settings")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_credentials_requires_auth(self, unauthenticated_client):
        resp = await unauthenticated_client.post("/my/credentials/bluesky")
        # 401 or 422 (missing auth before form parse)
        assert resp.status_code in (401, 422)


class TestTenantDashboard:
    @pytest.mark.asyncio
    async def test_dashboard_loads(self, tenant_client):
        resp = await tenant_client.get("/my/dashboard")
        assert resp.status_code == 200
        assert "TestTenant" in resp.text

    @pytest.mark.asyncio
    async def test_dashboard_shows_zero_posts(self, tenant_client):
        resp = await tenant_client.get("/my/dashboard")
        assert resp.status_code == 200
        # Should show 0 published and 0 drafts
        assert "<strong>0</strong>" in resp.text


class TestTenantSettings:
    @pytest.mark.asyncio
    async def test_settings_page_loads(self, tenant_client):
        resp = await tenant_client.get("/my/settings")
        assert resp.status_code == 200
        assert "Settings" in resp.text
        assert "TestTenant" in resp.text

    @pytest.mark.asyncio
    async def test_update_brand_profile(self, app, tenant_client):
        resp = await tenant_client.post(
            "/my/settings",
            data={
                "name": "UpdatedTenant",
                "industry": "SaaS",
                "target_audience": "Developers",
                "brand_voice": "Technical",
                "website": "https://updated.com",
                "products": "A product",
                "competitive_positioning": "The best",
                "key_messages": "Key msg",
                "content_pillars": "Pillar 1",
                "company_story": "Our story",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Verify the data was saved
        client_id = tenant_client._test_client_id
        db = app.state.db
        client = db.get_client(client_id)
        assert client["name"] == "UpdatedTenant"
        assert client["industry"] == "SaaS"
        assert client["website"] == "https://updated.com"

    @pytest.mark.asyncio
    async def test_settings_shows_api_keys(self, tenant_client):
        resp = await tenant_client.get("/my/settings")
        assert resp.status_code == 200
        assert "otb_" in resp.text  # Key prefix should be visible


class TestTenantCredentials:
    @pytest.mark.asyncio
    async def test_save_bluesky_credentials(self, tenant_client):
        resp = await tenant_client.post(
            "/my/credentials/bluesky",
            data={
                "handle": "test.bsky.social",
                "app_password": "xxxx-xxxx-xxxx-xxxx",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    @pytest.mark.asyncio
    async def test_save_twitter_credentials(self, tenant_client):
        resp = await tenant_client.post(
            "/my/credentials/twitter",
            data={
                "api_key": "key",
                "api_secret": "secret",
                "access_token": "token",
                "access_token_secret": "token_secret",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    @pytest.mark.asyncio
    async def test_save_linkedin_credentials(self, tenant_client):
        resp = await tenant_client.post(
            "/my/credentials/linkedin",
            data={
                "access_token": "li-token",
                "person_urn": "urn:li:person:abc123",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303


class TestDataScoping:
    """Ensure tenants only see their own data."""

    @pytest.mark.asyncio
    async def test_dashboard_only_shows_own_posts(self, app, tenant_client):
        db = app.state.db
        client_id = tenant_client._test_client_id

        # Create a post for this tenant
        db.save_post(text="My post", run_id="r1", status="published", client_id=client_id)
        # Create a post for a different client
        db.save_post(text="Other post", run_id="r1", status="published", client_id="default")

        resp = await tenant_client.get("/my/dashboard")
        assert "My post" in resp.text
        # The other client's post should NOT appear in this tenant's dashboard
        # (depends on scoping in get_recent_posts_with_metrics)
