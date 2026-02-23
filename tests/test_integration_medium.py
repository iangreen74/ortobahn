"""Tests for Medium integration client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ortobahn.integrations.medium import MediumClient


@pytest.fixture
def medium_client():
    return MediumClient(integration_token="test-token-123")


class TestMediumClient:
    def test_init(self, medium_client):
        assert medium_client.token == "test-token-123"
        assert medium_client._user_id is None

    def test_headers(self, medium_client):
        h = medium_client._headers()
        assert h["Authorization"] == "Bearer test-token-123"
        assert h["Content-Type"] == "application/json"

    @patch("ortobahn.integrations.medium.httpx.get")
    def test_get_user_id(self, mock_get, medium_client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"id": "user-abc"}}
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp

        uid = medium_client._get_user_id()
        assert uid == "user-abc"
        assert medium_client._user_id == "user-abc"

        # Second call should use cached value
        uid2 = medium_client._get_user_id()
        assert uid2 == "user-abc"
        assert mock_get.call_count == 1  # Only called once

    @patch("ortobahn.integrations.medium.httpx.post")
    @patch("ortobahn.integrations.medium.httpx.get")
    def test_post_article(self, mock_get, mock_post, medium_client):
        # Mock user ID fetch
        mock_get_resp = MagicMock()
        mock_get_resp.json.return_value = {"data": {"id": "user-abc"}}
        mock_get_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_get_resp

        # Mock post creation
        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = {
            "data": {
                "id": "post-123",
                "url": "https://medium.com/@user/test-article-abc123",
            }
        }
        mock_post_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_post_resp

        url, post_id = medium_client.post(
            title="Test Article",
            body_markdown="## Hello\n\nThis is a test.",
            tags=["test", "ai"],
        )

        assert url == "https://medium.com/@user/test-article-abc123"
        assert post_id == "post-123"

        # Verify payload
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["title"] == "Test Article"
        assert payload["contentFormat"] == "markdown"
        assert payload["publishStatus"] == "draft"
        assert payload["tags"] == ["test", "ai"]

    @patch("ortobahn.integrations.medium.httpx.post")
    @patch("ortobahn.integrations.medium.httpx.get")
    def test_post_with_publish_status(self, mock_get, mock_post, medium_client):
        mock_get_resp = MagicMock()
        mock_get_resp.json.return_value = {"data": {"id": "user-abc"}}
        mock_get_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_get_resp

        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = {"data": {"id": "p1", "url": "https://medium.com/p/p1"}}
        mock_post_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_post_resp

        medium_client.post(
            title="Public Article",
            body_markdown="Body",
            publish_status="public",
        )

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["publishStatus"] == "public"

    @patch("ortobahn.integrations.medium.httpx.post")
    @patch("ortobahn.integrations.medium.httpx.get")
    def test_tags_capped_at_five(self, mock_get, mock_post, medium_client):
        mock_get_resp = MagicMock()
        mock_get_resp.json.return_value = {"data": {"id": "user-abc"}}
        mock_get_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_get_resp

        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = {"data": {"id": "p2", "url": "https://medium.com/p/p2"}}
        mock_post_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_post_resp

        medium_client.post(
            title="T",
            body_markdown="B",
            tags=["a", "b", "c", "d", "e", "f", "g"],
        )

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert len(payload["tags"]) == 5

    def test_get_post_returns_none(self, medium_client):
        result = medium_client.get_post("some-id")
        assert result is None
