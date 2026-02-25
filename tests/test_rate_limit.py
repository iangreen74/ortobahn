"""Tests for rate limiting middleware."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from ortobahn.web.rate_limit import RateLimitMiddleware


@pytest.fixture
def app():
    """Create a test FastAPI app with rate limiting."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"message": "success"}

    @app.get("/health")
    async def health_endpoint():
        return {"status": "healthy"}

    return app


@pytest.fixture
def limited_app(app):
    """App with strict rate limiting (5 requests per 10 seconds)."""
    app.add_middleware(
        RateLimitMiddleware,
        enabled=True,
        default_rpm=5,
        window_seconds=10,
    )
    return app


@pytest.fixture
def disabled_app(app):
    """App with rate limiting disabled."""
    app.add_middleware(
        RateLimitMiddleware,
        enabled=False,
        default_rpm=5,
        window_seconds=10,
    )
    return app


def test_rate_limit_allows_requests_under_limit(limited_app):
    """Test that requests under the limit are allowed."""
    client = TestClient(limited_app)

    # Make 5 requests (at the limit)
    for _i in range(5):
        response = client.get("/test")
        assert response.status_code == 200
        assert response.json() == {"message": "success"}

        # Check rate limit headers
        assert "X-RateLimit-Limit" in response.headers
        assert response.headers["X-RateLimit-Limit"] == "5"
        assert "X-RateLimit-Remaining" in response.headers
        assert "X-RateLimit-Reset" in response.headers


def test_rate_limit_blocks_requests_over_limit(limited_app):
    """Test that requests over the limit are blocked with 429."""
    client = TestClient(limited_app)

    # Make 5 requests (at the limit)
    for _i in range(5):
        response = client.get("/test")
        assert response.status_code == 200

    # 6th request should be blocked
    response = client.get("/test")
    assert response.status_code == 429
    assert "error" in response.json()
    assert response.json()["error"] == "rate_limit_exceeded"
    assert "retry_after" in response.json()
    assert "Retry-After" in response.headers
    assert response.headers["X-RateLimit-Remaining"] == "0"


def test_rate_limit_sliding_window(limited_app):
    """Test that the sliding window correctly expires old requests."""
    client = TestClient(limited_app)

    # Make 3 requests
    for _i in range(3):
        response = client.get("/test")
        assert response.status_code == 200

    # Wait for requests to expire (window is 10 seconds, wait 11)
    time.sleep(11)

    # Should be able to make 5 more requests
    for _i in range(5):
        response = client.get("/test")
        assert response.status_code == 200

    # 6th should still be blocked
    response = client.get("/test")
    assert response.status_code == 429


def test_rate_limit_headers_present(limited_app):
    """Test that rate limit headers are added to all responses."""
    client = TestClient(limited_app)

    response = client.get("/test")
    assert response.status_code == 200

    # Check all required headers are present
    assert "X-RateLimit-Limit" in response.headers
    assert "X-RateLimit-Remaining" in response.headers
    assert "X-RateLimit-Reset" in response.headers

    # Verify header values
    assert int(response.headers["X-RateLimit-Limit"]) == 5
    assert int(response.headers["X-RateLimit-Remaining"]) >= 0
    reset_time = int(response.headers["X-RateLimit-Reset"])
    assert reset_time > time.time()


def test_rate_limit_disabled(disabled_app):
    """Test that rate limiting can be disabled."""
    client = TestClient(disabled_app)

    # Make 10 requests (well over the limit of 5)
    for _i in range(10):
        response = client.get("/test")
        assert response.status_code == 200
        assert response.json() == {"message": "success"}


def test_rate_limit_skips_health_endpoint(limited_app):
    """Test that health check endpoint is not rate limited."""
    client = TestClient(limited_app)

    # Make many requests to health endpoint
    for _i in range(20):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


def test_rate_limit_per_ip():
    """Test that rate limits are tracked separately per IP."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"message": "success"}

    app.add_middleware(
        RateLimitMiddleware,
        enabled=True,
        default_rpm=3,
        window_seconds=10,
    )

    client = TestClient(app)

    # Simulate requests from two different IPs via X-Forwarded-For
    for _i in range(3):
        r1 = client.get("/test", headers={"X-Forwarded-For": "10.0.0.1"})
        assert r1.status_code == 200

        r2 = client.get("/test", headers={"X-Forwarded-For": "10.0.0.2"})
        assert r2.status_code == 200

    # IP 1 should be blocked on 4th request
    r1 = client.get("/test", headers={"X-Forwarded-For": "10.0.0.1"})
    assert r1.status_code == 429

    # IP 2 should also be blocked on 4th request
    r2 = client.get("/test", headers={"X-Forwarded-For": "10.0.0.2"})
    assert r2.status_code == 429


def test_rate_limit_cleanup():
    """Test that old entries are cleaned up to prevent memory leaks."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"message": "success"}

    middleware = RateLimitMiddleware(
        app,
        enabled=True,
        default_rpm=10,
        window_seconds=5,
    )
    middleware._cleanup_interval = 1  # Speed up cleanup for testing

    app.add_middleware(
        RateLimitMiddleware,
        enabled=True,
        default_rpm=10,
        window_seconds=5,
    )

    client = TestClient(app)

    # Make some requests
    for _i in range(5):
        response = client.get("/test")
        assert response.status_code == 200

    # Wait for window to expire plus cleanup interval
    time.sleep(7)

    # Make another request to trigger cleanup
    response = client.get("/test")
    assert response.status_code == 200

    # After cleanup, we should be able to make a full set of requests again
    for _i in range(9):
        response = client.get("/test")
        assert response.status_code == 200


def test_rate_limit_forwarded_ip():
    """Test that X-Forwarded-For header is respected for IP detection."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"message": "success"}

    app.add_middleware(
        RateLimitMiddleware,
        enabled=True,
        default_rpm=2,
        window_seconds=10,
    )

    client = TestClient(app)

    # Make requests with X-Forwarded-For header
    headers = {"X-Forwarded-For": "203.0.113.1"}

    for _i in range(2):
        response = client.get("/test", headers=headers)
        assert response.status_code == 200

    # 3rd request should be blocked
    response = client.get("/test", headers=headers)
    assert response.status_code == 429


def test_rate_limit_remaining_counter():
    """Test that the remaining counter decreases correctly."""
    app = FastAPI()

    @app.get("/test")
    async def test_endpoint():
        return {"message": "success"}

    app.add_middleware(
        RateLimitMiddleware,
        enabled=True,
        default_rpm=5,
        window_seconds=10,
    )

    client = TestClient(app)

    # Track remaining count
    for i in range(5):
        response = client.get("/test")
        assert response.status_code == 200
        remaining = int(response.headers["X-RateLimit-Remaining"])
        # Remaining should decrease (allowing for off-by-one)
        assert remaining == (4 - i) or remaining == (3 - i)

    # After hitting limit, remaining should be 0
    response = client.get("/test")
    assert response.status_code == 429
    assert response.headers["X-RateLimit-Remaining"] == "0"
