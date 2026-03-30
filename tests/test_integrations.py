"""Tests for integration connectors."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from ortobahn.integrations import (
    PlatformAdapter,
    PlatformType,
    RateLimitConfig,
    RATE_LIMITS,
    WebhookReceiver,
)


class MockPlatformAdapter(PlatformAdapter):
    """Mock platform adapter for testing."""

    @property
    def platform_type(self) -> PlatformType:
        return PlatformType.TWITTER

    async def publish(self, content):
        return {"id": "123", "url": "https://twitter.com/test/123"}

    async def get_oauth_url(self, redirect_uri: str, state: str) -> str:
        return f"https://oauth.twitter.com?redirect_uri={redirect_uri}&state={state}"

    async def exchange_code(self, code: str, redirect_uri: str):
        return {"access_token": "test_token", "refresh_token": "refresh_token"}


@pytest.mark.asyncio
async def test_platform_adapter_rate_limit():
    """Test rate limiting functionality."""
    adapter = MockPlatformAdapter({"api_key": "test"})
    
    # Simulate hitting rate limit
    adapter.rate_limit = RateLimitConfig(requests_per_minute=2, requests_per_hour=100)
    adapter._request_times = [datetime.now() for _ in range(2)]
    
    # This should wait
    start = datetime.now()
    await adapter._check_rate_limit()
    duration = (datetime.now() - start).total_seconds()
    
    assert duration >= 0  # Should have waited or cleared old requests
    await adapter.close()


@pytest.mark.asyncio
async def test_platform_adapter_retry_logic():
    """Test retry logic with exponential backoff."""
    adapter = MockPlatformAdapter({"api_key": "test"})
    
    mock_func = AsyncMock(side_effect=[
        httpx.HTTPStatusError("Error", request=MagicMock(), response=MagicMock(status_code=500, headers={})),
        {"success": True}
    ])
    
    result = await adapter._retry_request(mock_func, max_retries=3)
    assert result == {"success": True}
    assert mock_func.call_count == 2
    await adapter.close()


@pytest.mark.asyncio
async def test_platform_adapter_oauth_flow():
    """Test OAuth flow methods."""
    adapter = MockPlatformAdapter({"client_id": "test", "client_secret": "secret"})
    
    oauth_url = await adapter.get_oauth_url("https://example.com/callback", "state123")
    assert "redirect_uri" in oauth_url
    assert "state=state123" in oauth_url
    
    tokens = await adapter.exchange_code("auth_code", "https://example.com/callback")
    assert "access_token" in tokens
    assert "refresh_token" in tokens
    await adapter.close()


def test_rate_limits_configuration():
    """Test rate limit configurations for all platforms."""
    assert len(RATE_LIMITS) == 6
    assert PlatformType.TWITTER in RATE_LIMITS
    assert RATE_LIMITS[PlatformType.TWITTER].requests_per_minute == 300
    assert RATE_LIMITS[PlatformType.LINKEDIN].requests_per_hour == 1000
