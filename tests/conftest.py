"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ortobahn.config import Settings
from ortobahn.db import Database
from ortobahn.llm import LLMResponse


@pytest.fixture
def test_settings():
    """Settings with test values - no real API keys."""
    return Settings(
        anthropic_api_key="sk-ant-test-key-1234567890",
        bluesky_handle="test.bsky.social",
        bluesky_app_password="test-xxxx-xxxx-xxxx",
        newsapi_key="test-newsapi-key",
        claude_model="claude-sonnet-4-5-20250929",
        post_confidence_threshold=0.7,
        max_posts_per_cycle=4,
    )


@pytest.fixture
def test_db(tmp_path):
    """Fresh SQLite DB for each test."""
    db = Database(tmp_path / "test.db")
    yield db
    db.close()


@pytest.fixture
def mock_llm_response():
    """Factory for creating LLMResponse objects."""

    def _make(text: str, input_tokens: int = 100, output_tokens: int = 200, thinking: str = ""):
        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model="claude-sonnet-4-5-20250929",
            thinking=thinking,
        )

    return _make


@pytest.fixture
def mock_bluesky_client():
    """Mock BlueskyClient that never hits the network."""
    from ortobahn.integrations.bluesky import PostMetrics

    client = MagicMock()
    client.login.return_value = None
    client.post.return_value = ("at://did:plc:test/app.bsky.feed.post/test123", "bafytest123")
    client.get_post_metrics.return_value = PostMetrics(
        uri="at://test",
        cid="bafytest",
        like_count=5,
        repost_count=2,
        reply_count=1,
        quote_count=0,
    )
    client.get_profile.return_value = {
        "handle": "test.bsky.social",
        "followers_count": 100,
        "posts_count": 25,
    }
    return client
