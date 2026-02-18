"""Tests for the sliding-window rate limiter middleware."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ortobahn.web.rate_limit import (
    DEFAULT_TIERS,
    GENERAL_TIER_NAME,
    RateLimitMiddleware,
    RateLimitStore,
    RateTier,
    _match_tier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(*, enabled: bool = True, default_rpm: int = 60, store: RateLimitStore | None = None) -> FastAPI:
    """Create a minimal FastAPI app with the rate-limit middleware."""
    app = FastAPI()

    app.add_middleware(
        RateLimitMiddleware,
        enabled=enabled,
        default_rpm=default_rpm,
        store=store,
    )

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/public/stats")
    async def public_stats():
        return {"total": 42}

    @app.post("/api/auth/login")
    async def login():
        return {"token": "abc"}

    @app.post("/api/auth/register")
    async def register():
        return {"ok": True}

    @app.post("/api/onboard")
    async def onboard():
        return {"client_id": "c1"}

    @app.get("/dashboard")
    async def dashboard():
        return {"page": "dashboard"}

    return app


# ---------------------------------------------------------------------------
# Store unit tests
# ---------------------------------------------------------------------------


class TestRateLimitStore:
    def test_allows_within_limit(self):
        store = RateLimitStore()
        for _ in range(5):
            allowed, count, retry = store.check("ip1:general", limit=5)
            assert allowed is True
        # Next one should be rejected
        allowed, count, retry = store.check("ip1:general", limit=5)
        assert allowed is False
        assert count == 6

    def test_retry_after_is_positive(self):
        store = RateLimitStore()
        for _ in range(3):
            store.check("ip1:general", limit=3)
        allowed, _, retry = store.check("ip1:general", limit=3)
        assert allowed is False
        assert retry > 0

    def test_different_keys_are_independent(self):
        store = RateLimitStore()
        for _ in range(3):
            store.check("ip1:general", limit=3)

        # ip2 should still be allowed
        allowed, count, _ = store.check("ip2:general", limit=3)
        assert allowed is True
        assert count == 1

    def test_cleanup_removes_expired_buckets(self):
        store = RateLimitStore(cleanup_interval=0)  # cleanup every call
        store.check("ip1:general", limit=100, window=0.01)
        assert "ip1:general" in store.buckets

        # Wait for window to expire, then trigger cleanup via another check
        time.sleep(0.02)
        store.check("ip2:general", limit=100, window=0.01)
        assert "ip1:general" not in store.buckets

    def test_window_expiry_resets_count(self):
        store = RateLimitStore()
        # Fill to the limit with a very short window
        for _ in range(3):
            store.check("ip1:general", limit=3, window=0.01)

        # Should be blocked immediately
        allowed, _, _ = store.check("ip1:general", limit=3, window=0.01)
        assert allowed is False

        # Wait for window to expire
        time.sleep(0.02)

        # Should be allowed again
        allowed, count, _ = store.check("ip1:general", limit=3, window=0.01)
        assert allowed is True
        assert count == 1


# ---------------------------------------------------------------------------
# Tier matching
# ---------------------------------------------------------------------------


class TestTierMatching:
    def test_health_matches_public_tier(self):
        tier = _match_tier("/health", DEFAULT_TIERS)
        assert tier is not None
        assert tier.name == "public"

    def test_public_prefix_matches(self):
        tier = _match_tier("/api/public/stats", DEFAULT_TIERS)
        assert tier is not None
        assert tier.name == "public"

    def test_auth_login_matches_auth_tier(self):
        tier = _match_tier("/api/auth/login", DEFAULT_TIERS)
        assert tier is not None
        assert tier.name == "auth"

    def test_auth_register_matches_auth_tier(self):
        tier = _match_tier("/api/auth/register", DEFAULT_TIERS)
        assert tier is not None
        assert tier.name == "auth"

    def test_onboard_matches_onboard_tier(self):
        tier = _match_tier("/api/onboard", DEFAULT_TIERS)
        assert tier is not None
        assert tier.name == "onboard"

    def test_unknown_path_returns_none(self):
        tier = _match_tier("/dashboard", DEFAULT_TIERS)
        assert tier is None

    def test_auth_confirm_falls_to_general(self):
        """Paths under /api/auth/ that aren't login/register use the general tier."""
        tier = _match_tier("/api/auth/confirm", DEFAULT_TIERS)
        assert tier is None  # general tier


# ---------------------------------------------------------------------------
# Middleware integration tests
# ---------------------------------------------------------------------------


class TestRateLimitMiddleware:
    def test_general_endpoint_limited(self):
        """Requests to general endpoints are capped at default_rpm."""
        store = RateLimitStore()
        app = _make_app(default_rpm=3, store=store)
        client = TestClient(app)

        for _ in range(3):
            resp = client.get("/dashboard")
            assert resp.status_code == 200

        resp = client.get("/dashboard")
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) > 0
        assert "rate limit" in resp.json()["detail"].lower()

    def test_auth_endpoint_limited(self):
        """Auth endpoints have a tighter limit (10/min by default)."""
        store = RateLimitStore()
        app = _make_app(default_rpm=60, store=store)
        client = TestClient(app)

        for _ in range(10):
            resp = client.post("/api/auth/login")
            assert resp.status_code == 200

        resp = client.post("/api/auth/login")
        assert resp.status_code == 429

    def test_onboard_endpoint_limited(self):
        """Onboard endpoint has the tightest limit (5/min)."""
        store = RateLimitStore()
        app = _make_app(default_rpm=60, store=store)
        client = TestClient(app)

        for _ in range(5):
            resp = client.post("/api/onboard")
            assert resp.status_code == 200

        resp = client.post("/api/onboard")
        assert resp.status_code == 429

    def test_public_endpoint_higher_limit(self):
        """Public endpoints have a generous 120/min limit."""
        store = RateLimitStore()
        app = _make_app(default_rpm=3, store=store)
        client = TestClient(app)

        # General endpoint gets blocked at 4th request
        for _ in range(3):
            client.get("/dashboard")
        resp = client.get("/dashboard")
        assert resp.status_code == 429

        # But /health still works (different tier, higher limit)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_and_public_share_tier(self):
        """/health and /api/public/* share the public tier bucket."""
        store = RateLimitStore()
        # Use custom tiers with a low public limit for testing
        tiers = (RateTier(name="public", requests_per_minute=4, prefixes=("/health", "/api/public/")),)
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, enabled=True, default_rpm=60, tiers=tiers, store=store)

        @app.get("/health")
        async def health():
            return {"ok": True}

        @app.get("/api/public/stats")
        async def stats():
            return {"total": 1}

        client = TestClient(app)

        # 2 to /health + 2 to /api/public/stats = 4, should be at the limit
        for _ in range(2):
            assert client.get("/health").status_code == 200
        for _ in range(2):
            assert client.get("/api/public/stats").status_code == 200

        # 5th request to either should be blocked
        resp = client.get("/health")
        assert resp.status_code == 429

    def test_disabled_mode_passes_through(self):
        """When rate limiting is disabled, all requests pass through."""
        store = RateLimitStore()
        app = _make_app(enabled=False, default_rpm=1, store=store)
        client = TestClient(app)

        # Even though default_rpm=1, disabled means no blocking
        for _ in range(10):
            resp = client.get("/dashboard")
            assert resp.status_code == 200

    def test_retry_after_header_present(self):
        """429 responses include a Retry-After header with a positive integer."""
        store = RateLimitStore()
        app = _make_app(default_rpm=1, store=store)
        client = TestClient(app)

        client.get("/dashboard")  # consume the limit
        resp = client.get("/dashboard")
        assert resp.status_code == 429
        retry_after = resp.headers.get("Retry-After")
        assert retry_after is not None
        assert int(retry_after) >= 1

    def test_different_ips_tracked_independently(self):
        """Requests from different IPs don't share rate-limit buckets."""
        store = RateLimitStore()
        app = _make_app(default_rpm=2, store=store)

        # We can't easily change the client IP in TestClient, so test via the store directly.
        # Confirm that the store keys include the IP component.
        allowed1, _, _ = store.check("10.0.0.1:general", limit=2)
        allowed2, _, _ = store.check("10.0.0.1:general", limit=2)
        allowed3, _, _ = store.check("10.0.0.1:general", limit=2)
        assert allowed1 is True
        assert allowed2 is True
        assert allowed3 is False  # blocked

        # Different IP is unaffected
        allowed_other, _, _ = store.check("10.0.0.2:general", limit=2)
        assert allowed_other is True

    def test_tiers_do_not_interfere(self):
        """Hitting the limit on one tier does not affect another tier."""
        store = RateLimitStore()
        app = _make_app(default_rpm=2, store=store)
        client = TestClient(app)

        # Exhaust the general tier
        for _ in range(2):
            client.get("/dashboard")
        resp = client.get("/dashboard")
        assert resp.status_code == 429

        # Auth tier is separate and still available
        resp = client.post("/api/auth/login")
        assert resp.status_code == 200

        # Public tier is also separate
        resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_rate_limit_settings_defaults(self):
        from ortobahn.config import Settings

        s = Settings(anthropic_api_key="sk-ant-test")
        assert s.rate_limit_enabled is True
        assert s.rate_limit_default == 60

    def test_rate_limit_settings_override(self):
        from ortobahn.config import Settings

        s = Settings(anthropic_api_key="sk-ant-test", rate_limit_enabled=False, rate_limit_default=120)
        assert s.rate_limit_enabled is False
        assert s.rate_limit_default == 120

    def test_load_settings_env_vars(self, monkeypatch):
        from ortobahn.config import load_settings

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
        monkeypatch.setenv("RATE_LIMIT_DEFAULT", "200")
        s = load_settings()
        assert s.rate_limit_enabled is False
        assert s.rate_limit_default == 200
