"""Tests for Substack integration client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ortobahn.integrations.substack import SubstackClient


@pytest.fixture
def substack_client():
    return SubstackClient(
        subdomain="testblog",
        session_cookie="test-session-cookie",
    )


@pytest.fixture
def substack_client_password():
    return SubstackClient(
        subdomain="testblog",
        email="test@example.com",
        password="secret123",
    )


class TestSubstackClient:
    def test_init(self, substack_client):
        assert substack_client.subdomain == "testblog"
        assert substack_client.base_url == "https://testblog.substack.com"
        assert substack_client._session_cookie == "test-session-cookie"

    def test_authenticate_with_cookie(self, substack_client):
        substack_client._authenticate()
        assert substack_client._authenticated is True

    @patch("ortobahn.integrations.substack.httpx.post")
    def test_authenticate_with_password(self, mock_post, substack_client_password):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        # Simulate cookie in response
        mock_cookie = MagicMock()
        mock_cookie.name = "substack.sid"
        mock_cookie.value = "new-session-id"
        mock_resp.cookies.jar = [mock_cookie]
        mock_post.return_value = mock_resp

        substack_client_password._authenticate()
        assert substack_client_password._authenticated is True
        assert substack_client_password._session_cookie == "new-session-id"

    def test_authenticate_no_credentials_raises(self):
        client = SubstackClient(subdomain="test")
        with pytest.raises(RuntimeError, match="session_cookie or email"):
            client._authenticate()

    def test_cookies(self, substack_client):
        cookies = substack_client._cookies()
        assert cookies == {"substack.sid": "test-session-cookie"}

    @patch("ortobahn.integrations.substack.httpx.post")
    def test_post_draft(self, mock_post, substack_client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": "draft-123", "slug": "test-article"}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        url, draft_id = substack_client.post(
            title="Test Article",
            body_markdown="## Hello\n\nContent here.",
            tags=["ai", "tech"],
        )

        assert url == "https://testblog.substack.com/p/test-article"
        assert draft_id == "draft-123"
        assert mock_post.call_count == 1  # Only draft, no publish

    @patch("ortobahn.integrations.substack.httpx.post")
    def test_post_and_publish(self, mock_post, substack_client):
        # First call: create draft
        mock_draft_resp = MagicMock()
        mock_draft_resp.json.return_value = {"id": "draft-456", "slug": "published-article"}
        mock_draft_resp.raise_for_status.return_value = None

        # Second call: publish
        mock_pub_resp = MagicMock()
        mock_pub_resp.raise_for_status.return_value = None

        mock_post.side_effect = [mock_draft_resp, mock_pub_resp]

        url, draft_id = substack_client.post(
            title="Published Article",
            body_markdown="Body",
            publish=True,
        )

        assert url == "https://testblog.substack.com/p/published-article"
        assert draft_id == "draft-456"
        assert mock_post.call_count == 2

    @patch("ortobahn.integrations.substack.httpx.post")
    def test_publish_failure_raises(self, mock_post, substack_client):
        # Draft succeeds
        mock_draft_resp = MagicMock()
        mock_draft_resp.json.return_value = {"id": "draft-789", "slug": "fail-pub"}
        mock_draft_resp.raise_for_status.return_value = None

        # Publish fails
        mock_post.side_effect = [mock_draft_resp, RuntimeError("Publish failed")]

        with pytest.raises(RuntimeError, match="Publish failed"):
            substack_client.post(
                title="Test",
                body_markdown="Body",
                publish=True,
            )
