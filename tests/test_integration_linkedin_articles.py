"""Tests for LinkedIn Articles integration client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ortobahn.integrations.linkedin_articles import LinkedInArticleClient


@pytest.fixture
def linkedin_article_client():
    return LinkedInArticleClient(
        access_token="test-token-abc",
        person_urn="urn:li:person:test123",
    )


class TestLinkedInArticleClient:
    def test_init(self, linkedin_article_client):
        assert linkedin_article_client.access_token == "test-token-abc"
        assert linkedin_article_client.person_urn == "urn:li:person:test123"

    def test_headers(self, linkedin_article_client):
        h = linkedin_article_client._headers()
        assert h["Authorization"] == "Bearer test-token-abc"
        assert "LinkedIn-Version" in h
        assert h["Content-Type"] == "application/json"

    def test_markdown_to_html(self):
        md = "## Hello\n\nThis is **bold** and `code`."
        html = LinkedInArticleClient._markdown_to_html(md)
        assert "<h2>" in html or "<h2" in html
        assert "<strong>bold</strong>" in html
        assert "<code>code</code>" in html

    @patch("ortobahn.integrations.linkedin_articles.httpx.post")
    def test_post_article(self, mock_post, linkedin_article_client):
        mock_resp = MagicMock()
        mock_resp.headers = {"X-RestLi-Id": "urn:li:ugcPost:12345"}
        mock_resp.json.return_value = {"id": "urn:li:ugcPost:12345"}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        url, urn = linkedin_article_client.post(
            title="Test LinkedIn Article",
            body_markdown="## Intro\n\nContent here.",
            tags=["ai", "marketing"],
        )

        assert "12345" in url or "12345" in urn
        assert urn == "urn:li:ugcPost:12345"
        mock_post.assert_called_once()

    @patch("ortobahn.integrations.linkedin_articles.httpx.post")
    def test_post_uses_ugc_endpoint(self, mock_post, linkedin_article_client):
        mock_resp = MagicMock()
        mock_resp.headers = {"X-RestLi-Id": "urn:li:ugcPost:99"}
        mock_resp.json.return_value = {"id": "urn:li:ugcPost:99"}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        linkedin_article_client.post(title="T", body_markdown="B")

        call_args = mock_post.call_args
        url_arg = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
        assert "ugcPosts" in str(url_arg)

    @patch("ortobahn.integrations.linkedin_articles.httpx.post")
    def test_post_includes_author_urn(self, mock_post, linkedin_article_client):
        mock_resp = MagicMock()
        mock_resp.headers = {"X-RestLi-Id": "urn:li:ugcPost:77"}
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        linkedin_article_client.post(title="T", body_markdown="B")

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["author"] == "urn:li:person:test123"
