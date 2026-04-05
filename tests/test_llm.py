from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from ortobahn.llm import LLMClient, LLMRequest, LLMResponse, ModelType, TokenBucket


@pytest.mark.asyncio
async def test_token_bucket_basic() -> None:
    """Test basic token bucket functionality."""
    bucket = TokenBucket(rate=10.0, capacity=10.0)

    await bucket.acquire(5.0)
    assert bucket.tokens == pytest.approx(5.0, abs=0.1)


@pytest.mark.asyncio
async def test_token_bucket_refill() -> None:
    """Test token bucket refills over time."""
    bucket = TokenBucket(rate=10.0, capacity=10.0)

    await bucket.acquire(10.0)
    assert bucket.tokens == pytest.approx(0.0, abs=0.1)

    await asyncio.sleep(0.5)
    await bucket.acquire(1.0)
    assert bucket.tokens < 10.0


@pytest.mark.asyncio
async def test_llm_client_generate() -> None:
    """Test LLM client generates responses."""
    mock_response = Mock()
    mock_response.content = [Mock(text="Test response")]
    mock_response.model = "claude-3-5-sonnet-20241022"
    mock_response.usage = Mock(input_tokens=10, output_tokens=20)
    mock_response.stop_reason = "end_turn"

    with patch("anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic.return_value = mock_client

        client = LLMClient(api_key="test-key", requests_per_minute=1000.0)
        request = LLMRequest(prompt="Test prompt")
        response = await client.generate(request)

        assert isinstance(response, LLMResponse)
        assert response.content == "Test response"
        assert response.usage["input_tokens"] == 10
        assert response.usage["output_tokens"] == 20


@pytest.mark.asyncio
async def test_llm_client_retry_on_rate_limit() -> None:
    """Test client retries on rate limit errors."""
    import anthropic

    mock_response = Mock()
    mock_response.content = [Mock(text="Success")]
    mock_response.model = "claude-3-5-sonnet-20241022"
    mock_response.usage = Mock(input_tokens=10, output_tokens=20)
    mock_response.stop_reason = "end_turn"

    call_count = 0

    async def mock_create(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise anthropic.RateLimitError("Rate limited", response=Mock(status_code=429), body=None)
        return mock_response

    with patch("anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_client.messages.create = mock_create
        mock_anthropic.return_value = mock_client

        client = LLMClient(api_key="test-key", requests_per_minute=1000.0)
        request = LLMRequest(prompt="Test")
        response = await client.generate(request)

        assert call_count == 2
        assert response.content == "Success"


@pytest.mark.asyncio
async def test_llm_client_batch_generate() -> None:
    """Test batch generation."""
    mock_response = Mock()
    mock_response.content = [Mock(text="Response")]
    mock_response.model = "claude-3-5-sonnet-20241022"
    mock_response.usage = Mock(input_tokens=10, output_tokens=20)
    mock_response.stop_reason = "end_turn"

    with patch("anthropic.AsyncAnthropic") as mock_anthropic:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic.return_value = mock_client

        client = LLMClient(api_key="test-key", requests_per_minute=1000.0)
        requests = [LLMRequest(prompt=f"Prompt {i}") for i in range(3)]
        responses = await client.batch_generate(requests)

        assert len(responses) == 3
        assert all(isinstance(r, LLMResponse) for r in responses)
