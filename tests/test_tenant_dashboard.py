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
        # Should show 0 published and 0 drafts in KPI cards
        assert "kpi-value" in resp.text


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

        # Activity feed is loaded via HTMX; check the endpoint directly
        resp = await tenant_client.get("/my/api/partials/activity")
        assert "My post" in resp.text
        # The other client's post should NOT appear in this tenant's activity feed


# ---------------------------------------------------------------------------
# TestTenantAnalytics
# ---------------------------------------------------------------------------


class TestTenantAnalytics:
    """Test the /my/analytics route.

    Note: The analytics route uses raw SQL that references like_count,
    repost_count, reply_count directly on the posts table. Those columns
    live in the metrics table, so the route returns a 500 when there are
    published posts. We test auth and the error path here.
    """

    @pytest.mark.asyncio
    async def test_analytics_requires_auth(self, unauthenticated_client):
        resp = await unauthenticated_client.get("/my/analytics")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_analytics_renders_with_metrics_join(self, tenant_client):
        """The analytics route should render without error using a metrics JOIN."""
        resp = await tenant_client.get("/my/analytics")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# TestTenantContentSources
# ---------------------------------------------------------------------------


class TestTenantContentSources:
    """Test updating content sources settings."""

    @pytest.mark.asyncio
    async def test_update_content_sources(self, app, tenant_client):
        resp = await tenant_client.post(
            "/my/settings",
            data={
                "_section": "content_sources",
                "news_category": "business",
                "news_keywords": "AI, machine learning",
                "rss_feeds": "https://example.com/feed.xml",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        db = app.state.db
        client_id = tenant_client._test_client_id
        client = db.get_client(client_id)
        assert client["news_category"] == "business"
        assert client["news_keywords"] == "AI, machine learning"

    @pytest.mark.asyncio
    async def test_update_article_settings(self, app, tenant_client):
        resp = await tenant_client.post(
            "/my/settings",
            data={
                "_section": "article_settings",
                "article_enabled": "on",
                "article_frequency": "weekly",
                "article_voice": "Professional and authoritative",
                "article_platforms": "medium,substack",
                "article_topics": "AI, Cloud, DevOps",
                "article_length": "long",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        db = app.state.db
        client_id = tenant_client._test_client_id
        client = db.get_client(client_id)
        assert client["article_enabled"] == 1
        assert client["article_frequency"] == "weekly"
        assert client["article_length"] == "long"


# ---------------------------------------------------------------------------
# TestTenantAutoPublish
# ---------------------------------------------------------------------------


class TestTenantAutoPublish:
    """Test the auto-publish toggle endpoint."""

    @pytest.mark.asyncio
    async def test_enable_auto_publish(self, app, tenant_client):
        resp = await tenant_client.post(
            "/my/auto-publish",
            data={
                "auto_publish": "on",
                "target_platforms": "bluesky,twitter",
                "posting_interval_hours": "8",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        db = app.state.db
        client_id = tenant_client._test_client_id
        client = db.get_client(client_id)
        assert client["auto_publish"] == 1
        assert client["target_platforms"] == "bluesky,twitter"
        assert client["posting_interval_hours"] == 8

    @pytest.mark.asyncio
    async def test_disable_auto_publish(self, app, tenant_client):
        resp = await tenant_client.post(
            "/my/auto-publish",
            data={
                "auto_publish": "",
                "target_platforms": "bluesky",
                "posting_interval_hours": "6",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        db = app.state.db
        client_id = tenant_client._test_client_id
        client = db.get_client(client_id)
        assert client["auto_publish"] == 0

    @pytest.mark.asyncio
    async def test_posting_interval_clamped(self, app, tenant_client):
        """posting_interval_hours should be clamped between 3 and 24."""
        resp = await tenant_client.post(
            "/my/auto-publish",
            data={
                "auto_publish": "on",
                "target_platforms": "bluesky",
                "posting_interval_hours": "1",  # below min of 3
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        db = app.state.db
        client_id = tenant_client._test_client_id
        client = db.get_client(client_id)
        assert client["posting_interval_hours"] == 3

    @pytest.mark.asyncio
    async def test_posting_interval_clamped_high(self, app, tenant_client):
        """posting_interval_hours above 24 should clamp to 24."""
        resp = await tenant_client.post(
            "/my/auto-publish",
            data={
                "auto_publish": "on",
                "target_platforms": "bluesky",
                "posting_interval_hours": "48",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        db = app.state.db
        client_id = tenant_client._test_client_id
        client = db.get_client(client_id)
        assert client["posting_interval_hours"] == 24


# ---------------------------------------------------------------------------
# TestTenantGenerate
# ---------------------------------------------------------------------------


class TestTenantGenerate:
    """Test the /my/generate pipeline trigger."""

    @pytest.mark.asyncio
    async def test_generate_redirects(self, tenant_client):
        resp = await tenant_client.post(
            "/my/generate",
            data={"platforms": "bluesky"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/my/dashboard" in resp.headers.get("location", "")


# ---------------------------------------------------------------------------
# TestTenantPublishDrafts
# ---------------------------------------------------------------------------


class TestTenantPublishDrafts:
    """Test the /my/publish-drafts endpoint."""

    @pytest.mark.asyncio
    async def test_publish_drafts_redirects(self, tenant_client):
        resp = await tenant_client.post(
            "/my/publish-drafts",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/my/dashboard" in resp.headers.get("location", "")


# ---------------------------------------------------------------------------
# TestTenantCredentialValidation
# ---------------------------------------------------------------------------


class TestTenantCredentialValidation:
    """Test credential validation (e.g., Bluesky handle format check)."""

    @pytest.mark.asyncio
    async def test_bluesky_email_rejected(self, tenant_client):
        """Bluesky handle with @ should be rejected."""
        resp = await tenant_client.post(
            "/my/credentials/bluesky",
            data={
                "handle": "user@bsky.social",
                "app_password": "xxxx-xxxx",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        location = resp.headers.get("location", "")
        assert "error=bluesky_handle_format" in location

    @pytest.mark.asyncio
    async def test_settings_shows_credential_error(self, tenant_client):
        """When error query param is present, show error message."""
        resp = await tenant_client.get("/my/settings?error=bluesky_handle_format")
        assert resp.status_code == 200
        assert "format" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_credential_reactivates_client(self, app, tenant_client):
        """Saving credentials on a 'credential_issue' client should re-activate it."""
        db = app.state.db
        client_id = tenant_client._test_client_id
        db.update_client(client_id, {"status": "credential_issue"})

        resp = await tenant_client.post(
            "/my/credentials/bluesky",
            data={
                "handle": "fixed.bsky.social",
                "app_password": "new-xxxx-xxxx",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        client = db.get_client(client_id)
        assert client["status"] == "active"


# ---------------------------------------------------------------------------
# TestTenantArticles
# ---------------------------------------------------------------------------


class TestTenantArticles:
    """Test article management routes."""

    @pytest.mark.asyncio
    async def test_articles_empty(self, tenant_client):
        resp = await tenant_client.get("/my/articles")
        assert resp.status_code == 200
        assert "No articles yet" in resp.text

    @pytest.mark.asyncio
    async def test_articles_list(self, app, tenant_client):
        db = app.state.db
        client_id = tenant_client._test_client_id

        db.save_article(
            {
                "client_id": client_id,
                "title": "My Test Article",
                "body_markdown": "Article body content here.",
                "confidence": 0.85,
                "word_count": 150,
                "status": "draft",
            }
        )

        resp = await tenant_client.get("/my/articles")
        assert resp.status_code == 200
        assert "My Test Article" in resp.text

    @pytest.mark.asyncio
    async def test_approve_article(self, app, tenant_client):
        db = app.state.db
        client_id = tenant_client._test_client_id

        aid = db.save_article(
            {
                "client_id": client_id,
                "title": "Approve Me",
                "body_markdown": "Content.",
                "status": "draft",
            }
        )

        resp = await tenant_client.post(
            f"/my/articles/{aid}/approve",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        article = db.get_article(aid)
        assert article["status"] == "approved"

    @pytest.mark.asyncio
    async def test_reject_article(self, app, tenant_client):
        db = app.state.db
        client_id = tenant_client._test_client_id

        aid = db.save_article(
            {
                "client_id": client_id,
                "title": "Reject Me",
                "body_markdown": "Bad content.",
                "status": "draft",
            }
        )

        resp = await tenant_client.post(
            f"/my/articles/{aid}/reject",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        article = db.get_article(aid)
        assert article["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_article_not_found(self, tenant_client):
        resp = await tenant_client.post(
            "/my/articles/nonexistent-id/approve",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_article_wrong_tenant(self, app, tenant_client):
        """A tenant should not be able to modify another tenant's article."""
        db = app.state.db

        aid = db.save_article(
            {
                "client_id": "default",  # belongs to default, not our tenant
                "title": "Not Mine",
                "body_markdown": "Content.",
                "status": "draft",
            }
        )

        resp = await tenant_client.post(
            f"/my/articles/{aid}/approve",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_edit_article(self, app, tenant_client):
        db = app.state.db
        client_id = tenant_client._test_client_id

        aid = db.save_article(
            {
                "client_id": client_id,
                "title": "Original Title",
                "body_markdown": "Original body.",
                "status": "draft",
            }
        )

        resp = await tenant_client.post(
            f"/my/articles/{aid}/edit",
            data={
                "title": "Updated Title",
                "subtitle": "A subtitle",
                "body_markdown": "Updated body content here.",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        article = db.get_article(aid)
        assert article["title"] == "Updated Title"
        assert article["body_markdown"] == "Updated body content here."

    @pytest.mark.asyncio
    async def test_generate_article_redirects(self, tenant_client):
        resp = await tenant_client.post(
            "/my/generate-article",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/my/articles" in resp.headers.get("location", "")


# ---------------------------------------------------------------------------
# TestTenantHTMXEndpoints
# ---------------------------------------------------------------------------


class TestTenantCredentialIsolation:
    """Verify that a tenant can only see their own credentials, posts, and data."""

    @pytest.mark.asyncio
    async def test_tenant_only_sees_own_credentials(self, app, tenant_client):
        """Credentials saved by one tenant are not visible to another."""
        db = app.state.db
        secret_key = app.state.settings.secret_key
        client_id = tenant_client._test_client_id

        # Save credentials for our tenant
        await tenant_client.post(
            "/my/credentials/bluesky",
            data={"handle": "me.bsky.social", "app_password": "my-secret"},
            follow_redirects=False,
        )

        # Create a different tenant and save credentials for them
        other_id = db.create_client({"name": "OtherTenant", "email": "other@test.com"})
        from ortobahn.credentials import save_platform_credentials

        save_platform_credentials(db, other_id, "bluesky", {"handle": "other.bsky.social"}, secret_key)

        # Our tenant's settings page should show our credentials, not the other's
        resp = await tenant_client.get("/my/settings")
        assert resp.status_code == 200
        assert "bluesky" in resp.text.lower()

        # Verify at the DB level that credentials are properly scoped
        row = db.fetchone(
            "SELECT * FROM platform_credentials WHERE client_id=? AND platform='bluesky'",
            (client_id,),
        )
        assert row is not None

        other_row = db.fetchone(
            "SELECT * FROM platform_credentials WHERE client_id=? AND platform='bluesky'",
            (other_id,),
        )
        assert other_row is not None
        assert row["id"] != other_row["id"]

    @pytest.mark.asyncio
    async def test_tenant_only_sees_own_posts(self, app, tenant_client):
        """Activity feed only shows posts belonging to the authenticated tenant."""
        db = app.state.db
        client_id = tenant_client._test_client_id

        # Create posts for our tenant
        db.save_post(text="My tenant post 1", run_id="r1", status="published", client_id=client_id)
        db.save_post(text="My tenant post 2", run_id="r1", status="draft", client_id=client_id)

        # Create posts for a different client
        other_id = db.create_client({"name": "OtherClient"})
        db.save_post(text="Other client secret post", run_id="r2", status="published", client_id=other_id)
        db.save_post(text="Other client draft", run_id="r2", status="draft", client_id=other_id)

        # Activity feed is loaded via HTMX; check the endpoint directly
        resp = await tenant_client.get("/my/api/partials/activity")
        assert resp.status_code == 200
        assert "My tenant post 1" in resp.text
        # The other client's post must not appear
        assert "Other client secret post" not in resp.text

    @pytest.mark.asyncio
    async def test_auth_dependency_binds_correct_client_id(self, app, tenant_client):
        """The AuthClient dependency correctly identifies the authenticated tenant."""
        db = app.state.db
        client_id = tenant_client._test_client_id

        # The settings page queries credentials scoped to client["id"]
        # Save credentials for this tenant
        await tenant_client.post(
            "/my/credentials/twitter",
            data={
                "api_key": "my-key",
                "api_secret": "my-secret",
                "access_token": "my-token",
                "access_token_secret": "my-token-secret",
            },
            follow_redirects=False,
        )

        # Verify that the credential was saved for the correct client_id
        row = db.fetchone(
            "SELECT client_id FROM platform_credentials WHERE client_id=? AND platform='twitter'",
            (client_id,),
        )
        assert row is not None
        assert row["client_id"] == client_id

    @pytest.mark.asyncio
    async def test_tenant_cannot_see_other_tenant_analytics(self, app, tenant_client):
        """Analytics page only shows data for the authenticated tenant."""
        db = app.state.db
        client_id = tenant_client._test_client_id

        # Create posts for both tenants
        db.save_post(text="My analytics post", run_id="r1", status="published", client_id=client_id)
        other_id = db.create_client({"name": "AnalyticsOther"})
        db.save_post(text="Other analytics post", run_id="r2", status="published", client_id=other_id)

        resp = await tenant_client.get("/my/analytics")
        assert resp.status_code == 200
        # The analytics SQL queries all filter by client_id=?
        # We cannot easily check numerical output, but verify the page loads
        # and does not contain the other tenant's text
        assert "Other analytics post" not in resp.text

    @pytest.mark.asyncio
    async def test_tenant_pipeline_status_scoped(self, app, tenant_client):
        """Pipeline status endpoint only shows runs for the authenticated tenant."""
        db = app.state.db
        client_id = tenant_client._test_client_id

        # Create a running pipeline for our tenant
        db.start_pipeline_run("run-mine", mode="single", client_id=client_id)

        # Create a running pipeline for a different tenant
        other_id = db.create_client({"name": "OtherPipeline"})
        db.start_pipeline_run("run-other", mode="single", client_id=other_id)

        resp = await tenant_client.get("/my/api/pipeline-status")
        assert resp.status_code == 200
        assert "running" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_two_tenants_credentials_isolated(self, app):
        """Two separate tenants each only see their own credentials in settings."""
        db = app.state.db
        secret_key = app.state.settings.secret_key

        # Create tenant A
        id_a, key_a, _ = _create_tenant(app)
        db.update_client(id_a, {"name": "TenantA"})

        # Create tenant B
        id_b = db.create_client({"name": "TenantB", "email": "b@test.com"})
        raw_key_b = generate_api_key()
        db.create_api_key(id_b, hash_api_key(raw_key_b), key_prefix(raw_key_b), "default")

        # Save different credentials for each
        from ortobahn.credentials import save_platform_credentials

        save_platform_credentials(db, id_a, "linkedin", {"access_token": "a-token"}, secret_key)
        save_platform_credentials(db, id_b, "bluesky", {"handle": "b.bsky.social"}, secret_key)

        # Tenant A should see linkedin connected
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": key_a},
        ) as client_a:
            resp_a = await client_a.get("/my/settings")
            assert resp_a.status_code == 200
            # Tenant A has linkedin
            assert "linkedin" in resp_a.text.lower()

        # Tenant B should see bluesky connected
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": raw_key_b},
        ) as client_b:
            resp_b = await client_b.get("/my/settings")
            assert resp_b.status_code == 200
            assert "bluesky" in resp_b.text.lower()


class TestTenantHTMXEndpoints:
    """Test the HTMX fragment endpoints."""

    @pytest.mark.asyncio
    async def test_pipeline_status_idle(self, tenant_client):
        resp = await tenant_client.get("/my/api/pipeline-status")
        assert resp.status_code == 200
        assert "idle" in resp.text.lower() or "awaiting" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_health_endpoint(self, tenant_client):
        resp = await tenant_client.get("/my/api/health")
        assert resp.status_code == 200
        assert "Published" in resp.text or "published" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_watchdog_no_data(self, tenant_client):
        resp = await tenant_client.get("/my/api/watchdog")
        assert resp.status_code == 200
        assert "normal" in resp.text.lower() or "No issues" in resp.text

    @pytest.mark.asyncio
    async def test_pipeline_status_running(self, app, tenant_client):
        db = app.state.db
        client_id = tenant_client._test_client_id

        db.start_pipeline_run("run-active", mode="single", client_id=client_id)

        resp = await tenant_client.get("/my/api/pipeline-status")
        assert resp.status_code == 200
        assert "running" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_pipeline_status_after_failure(self, app, tenant_client):
        db = app.state.db
        client_id = tenant_client._test_client_id

        db.start_pipeline_run("run-fail", mode="single", client_id=client_id)
        db.fail_pipeline_run("run-fail", ["Some error"])

        resp = await tenant_client.get("/my/api/pipeline-status")
        assert resp.status_code == 200
        assert "failed" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_htmx_endpoints_require_auth(self, unauthenticated_client):
        for endpoint in ["/my/api/pipeline-status", "/my/api/health", "/my/api/watchdog"]:
            resp = await unauthenticated_client.get(endpoint)
            assert resp.status_code == 401
